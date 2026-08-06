# Arquitetura transversal de drill-down da Torre — Gate G2

**Gate:** G2 — Task 1 (desenho, somente leitura, zero código).
**Baseline:** `main` @ `afc375d` (G1 concluído; dados jan–jul completos + agosto parcial).
**Orçamento do gate:** 3 tasks (1 desenho · 2 implementação · 3 QA + uma rodada consolidada de correção). Sem subgates.
**Referência estudada:** `b2b-gogroup/torre_b2b` (clone temporário read-only, removido após a análise).

Por que este documento existe: `GERENCIAL_DRILLDOWN_PLAN.md` registra o ciclo G1, fechado e específico da Gerencial. A frente G2 é **transversal** (contrato comum aos três conteúdos de detalhe + primeira expansão em Canais + telas futuras) e não cabe naquele plano sem reescrever histórico. Este documento é a fonte de verdade da frente transversal.

---

## 1. Estado atual (Task A)

### 1.1 O que já é compartilhado

- **Um único shell de diálogo**: `KpiDrilldownDialog.tsx` — portal para `document.body`, focus trap (mesmo seletor do MobileDrawer/U1), `inert` no shell, scroll lock, Escape, clique-fora, bottom-sheet no mobile (`items-end`) e centrado no desktop, `aria-modal`/`aria-labelledby`. Montado 2× na Gerencial (KPI e Pulso) e 1× em Canais. **Não existem "três modais" — existe 1 shell e 3 conteúdos.**
- **Preservação de filtros**: `mergeFilteredHref`/`buildPreservedQuery` (`lib/filters/nav-links.ts`) usados pelos 3 conteúdos e pela página de Marca; destino explícito sempre vence o filtro global; nada fora de `FILTER_QUERY_KEYS` é herdado.
- **Formatação/tons**: `fmtRefreshedAt`, `SEVERITY_LABEL/TONE` (executive-summary), `fmtBrl/fmtNumber`.

### 1.2 O que é específico de cada conteúdo (e deve continuar sendo)

| Conteúdo | Gênero | Estrutura |
|---|---|---|
| `KpiDrilldownContent` (U2) | **Definicional/decomposição** | valor grande → "Como é calculado" (KPI_META) → decomposição canal/marca → CTA |
| `InsightDrilldownContent` (G1) | **Diagnóstico** | o que aconteceu → métrica × referência → delta → evidência (stale) → por que a severidade → nota de confiança → CTA(s) |
| `ChannelComparisonDialogContent` (U3) | **Comparativo** | GMV/Pedidos vs mediana → MetricRows vs mediana/p75 → aviso de dado → chips de sinais → CTA |

São três gêneros semânticos distintos (definição ≠ diagnóstico ≠ comparação). Unificá-los num componente/registry único seria abstração antecipada.

### 1.3 Duplicações reais (nível de linha)

1. **Linha período + refreshed_at**: `KpiDrilldownContent:49-52` ≈ `ChannelComparisonDialogContent:63-67` (mesmo markup); `InsightDrilldownContent` (GroupDetail:125) tem período **mas não tem refreshed_at** — lacuna.
2. **Aviso âmbar** (mesmas classes `text-amber-* bg-amber-50 border-amber-200 rounded-lg px-3 py-1.5`): caveat do KPI (`:59`), `data_warning` de Canais (`:121-125`), `confidence_note` do Insight (`:112-116`).
3. **CTA final preservando filtros** (mesmas classes + `buildHref`): KPI `:70-75`, Canais `:142-147`, Insight `:160-167`.
4. **Linha de evidência** (label à esquerda, valor tabular à direita, sub-linha de referência): breakdowns do KPI ≈ `MetricRow` de Canais ≈ membros de grupo do Insight — com variações necessárias (share %, mediana, CTA por membro).

### 1.4 Diferenças semanticamente necessárias

- Severidade/“por quê” só existem no gênero diagnóstico (Insight) — KPI não tem severidade por definição.
- Mediana/p75 só existem no gênero comparativo (Canais) — vêm do contrato `channel_medians`.
- Modo lista→detalhe no mesmo diálogo é exclusivo do Pulso (grupos).

### 1.5 Fluxos que terminam sem explicação ou próxima ação

| Fluxo | Lacuna |
|---|---|
| Detalhe marca×canal (Canais) | Tem evidência (mediana/p75) mas **não tem diagnóstico em linguagem humana** nem explicação de por que cada sinal disparou (chips sem "por quê" — o `severityReason` existe só no Pulso). |
| Drill de KPI (Gerencial) | **Sem referência/comparação**: `overview.previous` e `gmv_mom_pct` já vêm carregados no contrato e não são exibidos no detalhe. |
| Detalhe de Insight (Pulso) | Sem `refreshed_at` na linha de contexto. |
| Página de Marca | Destino final da jornada; chega "fria", sem destacar o que motivou a navegação. Não tem drill próprio (aceitável nesta fase). |

### 1.6 Filtros: preservam ou perdem?

Todos os CTAs auditados preservam (`buildHref` = `mergeFilteredHref`): KPI→Canais/Financeiro, Insight→tela de origem (por membro e por grupo), Canais→`/brand/[brand]?brands=&channels=` (marca/canal da linha sobrescrevem, correto), Marca→volta a Canais. Produtos/Tempo Real/Inteligência/Operações estão **fora** do contrato de filtros por design (U5) — não é perda.

### 1.7 Dados já carregados × endpoint novo

- Os 3 conteúdos usam **somente dados já carregados** pela própria página (overview/brands; executive-summary; channelRows/channelMedians). A primeira expansão mantém isso.
- Exigiria **fetch novo (endpoint já existe)** e fica para fase futura: tendência diária marca×canal dentro do detalhe (`/performance/daily`).
- Exigiria **endpoint novo** e fica **fora do escopo do G2**: evidência transacional pedido-a-pedido no estilo torre_b2b (FunilDrilldown/PedidosModal).

---

## 2. Padrões relevantes do torre_b2b (Task B)

### 2.1 Copiar/adaptar

1. **Seção explícita de próximo passo** — o `insight-detail` da referência fecha com "Diagnóstico → Sinais identificados → **Ação sugerida**". Nossos detalhes terminam num link genérico ("Abrir…"); adotar um fecho de **ação em linguagem humana** + CTA.
2. **Evidência sempre contra uma referência nomeada** — a referência nunca mostra número solto; cada valor vem com etapa/mediana/status colorido semântico. Formalizar isso no nosso contrato (métrica principal sempre pareada com "Referência (X)").
3. **Affordance de clicável** — cards e linhas que abrem detalhe têm hover consistente (`hover:shadow-md hover:scale-[1.01]`). Nossa matriz de Canais usa botão "Detalhe" na coluna; manter o botão (melhor p/ a11y) e reforçar o affordance da linha.
4. **(Futuro, fora do G2)** Drill de evidência transacional: modal largo (max-w-6xl), tabela paginada 50/página, busca e filtros **dentro** do detalhe, fetch sob demanda. É o degrau seguinte da jornada ("evidência linha a linha") — depende de endpoint novo.

### 2.2 Onde a Torre já é melhor que a referência (não regredir)

- **A11y do shell**: a referência não tem focus trap/`inert`/aria/focus-return; nosso `KpiDrilldownDialog` tem tudo. Manter o shell único.
- **Filtros por URL**: a referência usa estado local (não compartilhável); nossa querystring preservada é superior — é a espinha da jornada.
- **null ≠ 0**: nossa distinção "Sem dado"/indisponível vs zero real não existe na referência.
- **Mobile**: bottom-sheet nosso vs modal centrado simples da referência.

### 2.3 Não aplicável ao negócio da Torre

Disparos operacionais (WhatsApp/edição inline/bulk), export XLSX em modal (lib `xlsx` foi removida no U4 por segurança), SWR (padrão de fetch próprio da Torre já resolve).

---

## 3. Contrato UX comum do detalhe (Task C)

Ordem canônica dos blocos num conteúdo de detalhe (nem todo gênero usa todos, mas a ordem nunca muda):

1. **Título + contexto** (no shell: `entidade · dimensão`);
2. **Linha de contexto**: período + filtros ativos + `refreshed_at`;
3. **Diagnóstico em linguagem humana** (1–2 frases: o que aconteceu e por que merece atenção);
4. **Métrica principal** (grande, tabular);
5. **Referência/comparação nomeada** (mediana do canal, p75, período anterior) + delta quando existir;
6. **Decomposição/evidência** (linhas label→valor→sub-referência);
7. **Qualidade/limitação do dado** (aviso âmbar único);
8. **Próximo passo**: CTA primário preservando filtros (+ CTA secundário opcional para aprofundar);
9. Severidade sempre com "por quê" textual (nunca chip sem explicação).

### 3.1 Decisão: composição, não registry

**Componentes pequenos de composição** (novos, em `src/components/drilldown/`):

| Componente | Substitui a duplicação |
|---|---|
| `DrilldownContextLine` | §1.3-1 (período + refreshed_at + filtros ativos) |
| `DrilldownMetricPair` | métrica principal × referência nomeada + delta (Insight já tem inline; Canais ganha) |
| `EvidenceRow` | §1.3-4 (label/valor tabular/sub-referência/tone) |
| `DataQualityNote` | §1.3-2 (aviso âmbar) |
| `DrilldownCta` | §1.3-3 (link com `buildHref`, estilos, aria, min-h-11) |

**Registry central: rejeitado nesta fase.** Justificativa: existem exatamente 3 conteúdos, de gêneros distintos; a repetição concreta demonstrada é de **blocos visuais** (4 duplicações em §1.3), não de fluxo/registro de insights; um registry exigiria abstrair semânticas diferentes sem um segundo caso real de cada gênero. Rever apenas quando um 4º conteúdo de detalhe nascer e repetir fluxo (não só visual).

---

## 4. Primeira expansão: **Canais** (Task D)

Escolhida sobre Marca porque:

1. **Zero fetch/endpoint novo** — `channelRows`/`channelMedians` já carregados alimentam tudo;
2. É o **destino nº 1** da Gerencial: 3 dos 4 KPIs e os insights `growth`/`drop`/`high_cost` apontam para `/canais` — é onde a pergunta "por quê?" chega primeiro;
3. As lacunas concretas estão lá (§1.5): detalhe sem diagnóstico humano, sinais sem explicação, sem comparação de período;
4. **Marca é o fim da jornada** — a melhoria de maior valor lá ("chegar quente": destacar o que motivou a navegação) depende do contexto emitido pelo detalhe de Canais, então vem na fatia seguinte, não antes.

**Jornada-alvo (querystring preservada de ponta a ponta, já garantida por `mergeFilteredHref`):**

```
Gerencial (Pulso) → insight (ex.: high_cost TikTok × marca)
  → detalhe do insight (o quê/por quê/referência)          [já existe — G1]
  → "Abrir na tela de origem" → /canais?channels=…&brands=…
  → matriz marca×canal → "Detalhe"
  → detalhe marca×canal com diagnóstico + sinais explicados + referência nomeada   [G2 Task 2]
  → "Abrir visão completa da marca" → /brand/[brand]?…      [já existe — U3]
```

---

## 5. Plano exato da Task 2 (Task E)

### 5.1 Arquivos

**Novos (pequenos, sem abstração além do listado):**
- `src/components/drilldown/DrilldownContextLine.tsx`
- `src/components/drilldown/DrilldownMetricPair.tsx`
- `src/components/drilldown/EvidenceRow.tsx`
- `src/components/drilldown/DataQualityNote.tsx`
- `src/components/drilldown/DrilldownCta.tsx`
- `src/lib/channel-signal-reasons.ts` — módulo **puro** com a explicação textual de cada sinal de canal (espelho do `severityReason` do G1, para os sinais de `canais-channel-metrics`), testável com node:test.

**Editados:**
- `ChannelComparisonDialogContent.tsx` — adota o contrato §3: linha de contexto, **diagnóstico em linguagem humana** (derivado dos sinais + posição vs mediana, sem métrica nova), GMV como `DrilldownMetricPair` (vs mediana do canal), `EvidenceRow`s, sinais com "por quê" (`channel-signal-reasons`), `DataQualityNote`, `DrilldownCta`.
- `KpiDrilldownContent.tsx` — substituição mecânica dos blocos duplicados pelos componentes; adiciona **referência vs período anterior** usando `overview.previous`/`gmv_mom_pct` **já carregados** (exibição de dado existente, nenhuma métrica nova).
- `InsightDrilldownContent.tsx` — substituição mecânica + `refreshed_at` na linha de contexto (prop nova vinda da página, dado já disponível).
- `app/page.tsx` / `app/canais/page.tsx` — só o wiring das props novas (refreshedAt/diagnóstico); nenhum estado novo além disso.

### 5.2 Critérios de aceite

- Zero fetch novo, zero endpoint, zero métrica inventada, zero mudança de backend/pipeline/banco;
- os 3 detalhes seguem a ordem canônica §3 e as 4 duplicações de §1.3 desaparecem (grep sem ocorrência dos blocos inline);
- estados **fresh/loading/error/empty preservados**: diálogo de Canais continua abrindo só com `dataIsFresh`; nenhum skeleton/fallback novo;
- **a11y**: shell intacto (focus trap/inert/focus-return), CTAs com nome acessível, alvos ≥44px (`min-h-11`), sinais com texto (não só cor);
- **desktop/mobile**: bottom-sheet preservado; sem scroll horizontal no painel;
- **testes de lógica** (node:test, sem browser): `channel-signal-reasons` (todos os sinais têm explicação; sinal desconhecido tem fallback), formatação do `DrilldownMetricPair` (null-safety: nunca NaN/Infinity/"0 fabricado"), `EvidenceRow` tone mapping; testes existentes de pulse/canais continuam verdes;
- `npm test` + `typecheck` + `build` verdes.

### 5.3 Task 3 (QA + rodada única de correção)

QA visual com Playwright temporário (mesmo setup U6/G1, porta 3100, `%TEMP%`): jornada completa Gerencial→insight→Canais→detalhe→Marca em desktop 1440×900 e mobile 390×844; foco/Escape/clique-fora; leitura com filtros na URL após cada salto. Uma rodada consolidada de correção; sem G2.1.

---

## 6. Registro da Task 2 (implementada em 05/08/2026 — QA visual pendente na Task 3)

**Componentes efetivamente extraídos** (todos com ≥2 consumidores reais nesta implementação):

| Componente | Consumidores |
|---|---|
| `drilldown/DrilldownContextLine` | KPI, Canais, Insight (que ganhou o `refreshed_at` que faltava) |
| `drilldown/DataQualityNote` | caveat do KPI, `data_warning` de Canais, `confidence_note` do Insight |
| `drilldown/DrilldownCta` | KPI, Canais, Insight (grupo unitário) — alvo ≥44px padronizado nos três |
| `drilldown/EvidenceRow` | MetricRows/p75 de Canais + 4 listas de decomposição do KPI |

**Proposto no desenho e NÃO criado:** `DrilldownMetricPair` — o par GMV/Pedidos de Canais e o grid métrica×referência do Insight têm estruturas diferentes e apenas 1 consumidor convincente cada; ficaram como markup local (regra anti-overengineering da Task 2). Os CTAs por membro do Insight também permaneceram locais (semântica própria: aria-label por marca + largura mínima).

**Canais é a primeira aplicação completa do contrato §3**: contexto → diagnóstico humano (novo `lib/channel-signal-reasons.ts`, puro: descreve sinais com evidência do MESMO canal, p75 inclusivo dito como "no p75 ou acima", indisponibilidade explícita, zero threshold novo) → métricas principais → evidências vs referências → sinais explicados (chip + razão, nunca chip mudo) → qualidade → próximo passo + CTA.

**Mudanças mínimas nos outros dois:** KPI ganhou "Referência (período anterior)" **só para GMV** (`prev_gmv`/`gmv_mom_pct` já carregados; demais KPIs mostram "Comparação indisponível" — nada aproximado); Insight ganhou `refreshed_at` da mesma resposta fresca do executive-summary (`execFresh ? execSummary.period.refreshed_at : null`).

**Testes:** `tests/channel-signal-reasons.test.ts` (11 casos: sem sinal, evidência de custo, p75 inclusivo/exclusivo, múltiplos sinais, métrica/referência ausente, null≠zero, sem_dado, sinal desconhecido) + `tests/drilldown-wiring.test.ts` (regressão estática: zero fetch nos conteúdos, um único shell `KpiDrilldownDialog`, refreshed_at do insight vindo do executive-summary com gate `execFresh`, diálogo de Canais gated por `dataIsFresh`, CTAs via `buildHref`). Suíte completa: 412 testes, typecheck e build verdes. **QA visual: pendente — reservado para a Task 3.**

## 7. Registro da Task 3 — QA visual e rodada única (05/08/2026) — **Gate G2 concluído tecnicamente**

**QA visual executado** (Playwright + Chromium temporários e isolados em `%TEMP%`, Torre em build de produção na porta 3100, API pública read-only via interceptação local só para devolver o header CORS — nenhum backend alternativo, nenhuma escrita): desktop **1440×900** e mobile **390×844**, período 07/2026 com `compare=true`.

**Fluxos validados (1º QA e reteste, ambos limpos):**
1. *Gerencial→KPI* (4 KPIs): contexto+refreshed_at, referência do período anterior no GMV e "Comparação indisponível" nos demais, decomposições, CTA com filtros, Escape/backdrop/foco inicial e devolução, sem overflow mobile.
2. *Gerencial→Pulso*: insight direto e "Ver todos"→grupo→voltar, `refreshed_at` do executive-summary no detalhe, CTAs com filtros, foco nas trocas internas.
3. *Canais→matriz→Detalhe→Marca*: linha não-clicável (só o botão "Detalhe"), ordem completa do contrato §3, referências do mesmo canal, p75 inclusivo em dado real ("no p75 (25,4%) ou acima"), rolagem interna, CTA→página da marca preservando brand/canal/datas/compare.
- Em tudo: **0 erro de console, 0 hydration, 0 host inesperado, 1 único shell**.

**Findings do 1º QA:** nenhum visual/funcional no app (os apontamentos iniciais eram artefatos do harness — case de `text-transform: uppercase` no `innerText` e colisão de seletor com o MobileDrawer). **Rodada única consolidada** = os 3 findings semânticos obrigatórios:
1. `ads_subutilizado` agora espelha a regra real do backend (GMV ≥ mediana do canal; Ads/GMV < mediana ou ausente — ausência dita como parte da regra; ROAS ≥ mediana / ausente / gasto zero), só afirmando evidências disponíveis;
2. sinal `custo_alto`/`frete_alto` com valor **abaixo** do p75 carregado passa a declarar **inconsistência entre o sinal e a referência exibida** (sem reclassificar, sem fingir corte) — contraprova em teste substituiu o caso que legitimava a contradição;
3. próximo passo corrigido para "…ver a evolução diária e **os indicadores disponíveis da marca** no período" (nada é prometido que a página de Marca não tem).

**Reteste pós-correção:** build refeito, 3 fluxos × 2 viewports re-executados — limpo; explicação nova do Ads e redação nova visíveis com dado real. **Validações finais:** 416 testes / 0 falhas, typecheck, build; zero dependência nova, package-lock intacto, zero fetch novo, zero mudança fora de `apps/web`+`docs`. Artefatos do QA (screenshots/logs/Chromium) somente em `%TEMP%`, fora do Git.

**Dívidas registradas (não implementadas):** "chegada quente" na página de Marca; drill transacional (endpoint novo); tendência diária no detalhe; explicação de `roas_forte` poderia adotar a redação inclusiva "na mediana ou acima" (hoje mostra os dois números sem afirmar o corte).

## 8. Gate G3 — Task 1: página de Marca "chegando quente" (desenho, 05/08/2026)

Somente auditoria e desenho — zero implementação. Fecha a dívida registrada em §7 ("chegada quente na Marca").

### 8.1. Precheck do DQ2 em produção (read-only) — **BLOQUEADOR OPERACIONAL**

| Item | Resultado |
|---|---|
| Front — Qualidade/TikTok como indisponível | ✅ publicado (`Cancelamento TK`, `N/D`, "nesta fonte", "taxa zero" no chunk `app/qualidade/page-e394…`) |
| Front — "GMV com cobertura regional" | ✅ publicado; rótulo antigo "GMV Regional" **ausente** do bundle |
| Front — UF relativa aos elegíveis / escopo | ✅ "UF preenchida", "Escopo regional", "fora do escopo" presentes |
| Front — TikTok isolado = `not_applicable` | ✅ lógica publicada (`regioes-scope` no chunk de Regiões) |
| **API — `custo_alto` sem base válida** | ❌ **backend ainda com a lógica pré-DQ2** |

Prova comportamental: em `/canais?channels=tiktok` na janela **01–05/08** (e em 04/08 isolado) o custo de marketplace é **0,0% em todas as marcas** (mediana 0,0 · p75 0,0) e a API **emite `custo_alto` para as 5 marcas** — exatamente o falso positivo que a guarda do DQ2 elimina. Em julho a distribuição tem dispersão real (29,3 · 25,1 · 24,9 · 24,6 · 20,5; mediana 24,9 · p75 25,1), então julho **não discrimina** as duas versões da regra: os dois sinais são legítimos nas duas.

**Consequência (na data do desenho):** o commit `04493d5` estava em `origin/main` e o front publicado, mas o backend do Render **não**. Foi classificado como **bloqueador operacional** e nenhum deploy foi feito por este gate.

**RESOLVIDO em 06/08/2026.** O deploy manual do backend no Render foi concluído no commit `04493d5` (serviço Live) e a verificação read-only pós-deploy **passou nos dois critérios**: (1) `/canais?channels=tiktok` em 01–05/08, no cenário degenerado `custo = mediana = p75 = 0`, retorna **0 sinais `custo_alto`** (antes 5/5); (2) `/executive-summary` traz os **5 campos aditivos do G1** (`category`, `reference_value`, `reference_kind`, `delta_abs`, `confidence_note`) em todos os insights, e a frase que o G1 removeu ("…acima do usual") não aparece mais. Achado colateral registrado: o G1 também nunca havia chegado à produção — os dois gates entraram juntos neste deploy. **Bloqueador encerrado; Task 2 desbloqueada e implementada (§8.7).**

### 8.2. Diagnóstico da jornada e o ponto exato da perda

CTAs que terminam em `/brand/[brand]` (todos via `buildHref` → `mergeFilteredHref`):

| Origem | Href gerado | Contexto que existe na origem |
|---|---|---|
| `ChannelComparisonDialogContent:139` (detalhe marca×canal) | `/brand/{brand}?brands={brand}&channels={channel}` | **diagnóstico completo**: sinais, mediana/p75 do canal, `data_warning`, headline |
| `canais/page.tsx:722/830/946` (tabelas por canal) | idem, `channels` fixo | linha da tabela |
| `financeiro/page.tsx:393/468/555` | idem | linha da tabela |
| `BrandPerformanceTable` / `BrandTable` (Gerencial) | `/brand/{brand}` + query preservada | linha da tabela |
| Pulso (`InsightDrilldownContent`) | `insight.href` do backend → hoje `/canais?brands=…`, **nunca** `/brand/…` | tipo do insight, severidade, referência |

**Ponto exato da perda:** no `<Link>` do CTA. `mergeFilteredHref` transporta **apenas** `FILTER_QUERY_KEYS` (`channels`, `brands`, `date_from`, `date_to`, `compare`) mais o que o destino trouxer explicitamente. O href do detalhe traz só `brands` e `channels` — ou seja, a Marca recebe **o quê** (marca, canal, período) e nunca **o porquê** (sinal, origem). A página não tem nenhum estado/prop de entrada além da rota e da querystring.

Consequência secundária: essa mesma regra é a razão pela qual o contexto **não vaza** pela sidebar — `buildPreservedQuery` itera só `FILTER_QUERY_KEYS`, então qualquer parâmetro novo fora dessa lista é descartado ao navegar pelo menu. **O requisito "não sobreviver à navegação pela sidebar" já é garantido pela arquitetura atual, sem alteração.**

Dados na Marca hoje: `/performance/daily` (por marca × canal × período) + `fetchBrandDetail(brand, period)`; estados `dailyIsFresh` (`resolvedDailyKey === dailyRequestKey`), `detailLoading`, `isLive`, skeletons. Link de volta **já existe** (`backToCanais = mergeFilteredHref("/canais", …)`). **Nenhuma seção tem `id`** — só `<h2>`; ancoragem exigirá adicionar `id`s.

**Capacidade real da Marca por tipo de sinal** (base para não prometer o que não existe):

| Sinal/insight de origem | A Marca tem evidência? | Seção-alvo |
|---|---|---|
| `drop`, `growth` (GMV) | **sim** | "Mix de Canal — GMV Diário" + "Últimos 7 Dias" |
| `ads_subutilizado`, `roas_forte` | **parcial** — KPI Ad Spend/ROAS existe para ML/Shopee; "N/D para TikTok Shop" | KPIs |
| `custo_alto` / `high_cost` | **NÃO** — não há custo/fee por marca nesta tela | nenhuma → texto honesto + CTA de volta |
| `frete_alto` | **NÃO** | nenhuma → idem |
| `high_cancel_rate` | **NÃO** (qualidade vive em `/qualidade`) | nenhuma → idem |
| `sem_dado`, `stale_data` | n/a | nota de qualidade |

### 8.3. Contrato de contexto — decisão

Opções comparadas:

| Opção | Veredito |
|---|---|
| **A. parâmetros explícitos e allowlisted na URL** | **ESCOLHIDA** — compartilhável, sobrevive a back/forward, zero estado global, zero endpoint, e a allowlist existente já impede vazamento pela sidebar |
| B. fragmento/âncora + parâmetros mínimos | **parcialmente adotada**: a âncora é usada para levar à seção, mas a seção é **derivada** do sinal, não transportada (menos superfície e impossível apontar seção inexistente) |
| C. fetch de endpoint existente na Marca | **rejeitada** — "de onde o usuário veio" é fato da navegação, não do servidor; recalcular o sinal exigiria buscar `/canais` inteiro (todas as marcas) só para uma linha, duplicando lógica e custo |

**Parâmetros allowlisted** (prefixo `ctx_`, deliberadamente **fora** de `FILTER_QUERY_KEYS`):

| Param | Domínio (enum fechado) | Papel |
|---|---|---|
| `ctx_from` | `canais` \| `gerencial` | origem da jornada (rótulo e destino do CTA de volta) |
| `ctx_signal` | `drop` \| `growth` \| `custo_alto` \| `high_cost` \| `frete_alto` \| `ads_subutilizado` \| `roas_forte` \| `high_cancel_rate` \| `sem_dado` \| `stale_data` | motivo, em linguagem humana |
| `ctx_channel` | `tiktok` \| `ml` \| `shopee` | canal do sinal — usado para **detectar incompatibilidade** quando o usuário troca o filtro |
| `ctx_brand` | slug de marca conhecida | marca do sinal — idem, contra a troca de marca pelos pills |

Regras duras: **nenhum valor monetário, percentual, mediana/p75, texto livre, mensagem ou JSON na URL**; a querystring **nunca** é fonte de verdade de métrica — o bloco de contexto **não exibe número algum**, só o motivo qualitativo, canal e período (os números continuam vindo dos fetches da própria página). Parâmetro desconhecido/valor fora do enum → **ignorado silenciosamente**. Sem contexto → página idêntica à atual. `FILTER_QUERY_KEYS` **não** é ampliada (justificativa: manter os `ctx_*` fora dela é o que garante o descarte na sidebar e impede que o contexto contamine outras telas). Sem registry, sem context provider.

### 8.4. Experiência

**Com contexto válido** — bloco compacto logo abaixo do cabeçalho da marca (nunca antes dos KPIs, nunca substituindo-os):
"**Você chegou aqui por:** {motivo em linguagem humana} · {canal} · {período}" + linha indicando a seção relevante (ou a ausência dela, honestamente) + **CTA "Voltar à evidência em {origem}"** (reaproveita `DrilldownCta` e a lógica de `backToCanais`) + âncora/realce leve na seção-alvo quando ela existe. Não abre modal, não repete resumo executivo, não fabrica evidência; limitação declarada via `DataQualityNote` quando o sinal é de dado (`sem_dado`/`stale_data`) ou quando a Marca **não** tem a evidência (custo/frete/cancelamento).

**Sem contexto:** nada é renderizado — nenhum espaço vazio, banner genérico ou texto de "chegada quente".

**Contexto inválido/desatualizado** (enum inválido, `ctx_brand` ≠ rota, `ctx_channel` fora do filtro atual): parâmetros ignorados em silêncio, sem erro, sem dado de outra marca/canal, filtros seguem funcionando. Trocar marca ou canal de forma incompatível **descarta** o bloco.

### 8.5. Matriz de jornadas

| # | Jornada | Params | Texto | Seção | CTA de volta | Esperado |
|---|---|---|---|---|---|---|
| 1 | Gerencial → KPI → Canais → detalhe → Marca | `brands`,`channels`,datas,`compare` + `ctx_from=canais`,`ctx_signal`,`ctx_channel`,`ctx_brand` | "Você chegou aqui por: custo de marketplace no topo do canal · Shopee · 01–31/07" | custo: nenhuma (declarado) | "Voltar à evidência em Canais" | bloco + KPIs intactos |
| 2 | Gerencial → Pulso individual → Canais → detalhe → Marca | idem, `ctx_from=canais` | idem ao sinal da linha | conforme §8.2 | idem | idem |
| 3 | Gerencial → Pulso agrupado → membro → Canais → detalhe → Marca | idem | idem | idem | idem | idem (membro não muda o contrato) |
| 4 | Canais direto → detalhe → Marca | idem | idem | idem | idem | idem |
| 5 | URL direta `/brand/kokeshi` sem contexto | nenhum `ctx_*` | — | — | `backToCanais` atual | página **idêntica** à de hoje |
| 6 | Contexto inválido (`ctx_signal=xyz`) | inválido | — | — | atual | ignora em silêncio |
| 7 | Troca de marca pelos pills | `ctx_brand` ≠ rota | — | — | atual | bloco **desaparece** |
| 8 | Troca de canal no filtro | `ctx_channel` ∉ filtro | — | — | atual | bloco **desaparece** |
| 9 | Mobile 390×844 | idem 1 | idem, empilhado | idem | idem | sem overflow; alvo ≥44px |
| 10 | Botão voltar do navegador | histórico | volta ao detalhe em Canais | — | — | filtros preservados (query na URL) |
| 11 | Sidebar → outra tela → volta | `ctx_*` descartados | — | — | — | contexto **não** sobrevive |

### 8.6. Plano exato da Task 2 (bloqueada até o backend do DQ2 ir a produção)

**Novo (1 módulo puro):** `apps/web/src/lib/brand-arrival-context.ts` — `parseBrandArrivalContext(searchParams, routeBrand, selectedChannels)` → `BrandArrivalContext | null`, com enums fechados, validação de compatibilidade, mapa `signal → { motivo, seção-alvo | null, temEvidência }` e builder do href de retorno. Sem React, testável com node:test.

**Novo (1 componente pequeno):** `apps/web/src/components/BrandArrivalBanner.tsx` — apresentação do bloco reusando `DrilldownContextLine`, `DataQualityNote` e `DrilldownCta` do G2. Não cria primitive novo.

**Editados (3):** `app/brand/[brand]/page.tsx` (parse + render do bloco + `id`s nas seções-alvo); `src/components/ChannelComparisonDialogContent.tsx` (anexar `ctx_*` **apenas** ao CTA da marca); `apps/web/package.json` (registrar o teste).

**Não muda:** backend, endpoints, `FILTER_QUERY_KEYS`, `KpiDrilldownDialog`, primitives do G2, `channel-signal-reasons.ts`, demais telas. **Zero endpoint, zero fetch novo, zero dependência.**

**Testes:** unitários do parse (enum inválido → null; `ctx_brand` divergente → null; `ctx_channel` fora do filtro → null; sem params → null; sinal sem evidência na Marca → `temEvidência=false`; href de retorno preserva filtros e **não** propaga `ctx_*`) + estáticos de wiring (bloco só com contexto válido; `ctx_*` fora de `FILTER_QUERY_KEYS`; um único shell de diálogo; nenhum número vindo da URL).

**Critérios de aceite:** as 11 jornadas de §8.5; null ≠ zero; frescor de requisição intacto; a11y (bloco anunciado, foco/âncora, alvos ≥44px); desktop+mobile sem overflow; querystring compartilhável; suítes web/API, typecheck e build verdes.

**Task 3:** QA visual em navegador (jornadas 1, 5, 6, 7, 8 em desktop e mobile) + uma rodada consolidada de correção.

### 8.7. Task 2 implementada (06/08/2026) — QA visual pendente na Task 3

**Ajuste de escopo aplicado:** o contrato aceita **somente `ctx_from=canais`**. `gerencial` não foi implementado porque não existe produtor real hoje (o Pulso aponta para `/canais`, nunca para `/brand/…`) — não se cria enum sem wiring. A jornada continua podendo começar na Gerencial; o contexto mostrado na Marca representa **a evidência imediata escolhida no detalhe marca × canal**. A **propagação transitiva desde a Gerencial permanece dívida futura**.

**Criado:** `src/lib/brand-arrival-context.ts` (módulo puro) e `src/components/BrandArrivalBanner.tsx` (bloco compacto, não-modal, reusando `DrilldownContextLine`/`DataQualityNote`/`DrilldownCta`). **Editado:** o CTA da marca em `ChannelComparisonDialogContent.tsx`, `app/brand/[brand]/page.tsx` (parse + banner + `id`/`scroll-mt-24` na seção "Período selecionado") e a lista de testes do `package.json`.

**Contrato final:** `ctx_from=canais` · `ctx_signal ∈ {custo_alto, frete_alto, ads_subutilizado, sem_dado, roas_forte}` · `ctx_channel ∈ {tiktok, ml, shopee}` · `ctx_brand ∈ {barbours, kokeshi, apice, lescent, rituaria}`. Todos obrigatórios; parâmetro **repetido é ambíguo e invalida** o contexto; marca precisa bater com a rota e canal precisa estar no filtro atual. **Nenhum dígito trafega na querystring** (teste dedicado) — a URL nunca é fonte de verdade de métrica e o banner não exibe número algum. `ctx_*` **não** entra em `FILTER_QUERY_KEYS`, então a sidebar e os links da própria Marca descartam o contexto.

**Compatibilidade sinal × canal** (rodada de correção da Task 2): como a URL é **entrada não confiável**, não basta o produtor legítimo nunca gerar a combinação. `SIGNALS_BY_CHANNEL` espelha a aplicabilidade do contrato vigente (`_ADS_APPLICABLE` e `_SHIPPING_APPLICABLE` são `false` para TikTok; `_COST_APPLICABLE` é `true` nos três) — **TikTok aceita apenas `custo_alto` e `sem_dado`**; ML e Shopee aceitam os cinco. A guarda vale nos **dois lados**: `parseBrandArrivalContext` devolve `null` e `buildArrivalParams` devolve `""` para combinação incompatível. Nenhum threshold, sinal ou regra de negócio foi criado.

**Redação neutra de `ads_subutilizado`:** a descrição passou a ser "sinal de Ads subutilizado no canal" — a regra do canal também dispara quando o percentual de Ads está **ausente** (a ausência conta como subutilização) ou quando o **gasto é zero**, então "abaixo da mediana" não seria verdade em todos os ramos. A nota continua explicando que esta página mostra **apenas o investimento do período** e que a comparação com o canal e o diagnóstico completo permanecem na matriz por canal. Nenhuma mediana/percentual é transportada ou recalculada.

**Prioridade de sinal** (a URL não transporta array): espelha a classificação do G2 — atenção antes de destaque, na ordem `custo_alto → frete_alto → ads_subutilizado → sem_dado → roas_forte`. Sem sinal conhecido, o CTA funciona **sem** `ctx_*`. Nenhum threshold criado, nenhuma severidade reclassificada.

**Honestidade por capacidade:** `ads_subutilizado` é o **único** sinal de Canais com evidência real nesta página (KPI "Ad Spend" do período; "N/D" no TikTok) e ganha a âncora `#marca-periodo` + CTA "Ver investimento do período", com a ressalva de que **a comparação contra a mediana do canal fica em Canais**. `custo_alto`, `frete_alto`, `sem_dado` e `roas_forte` **não têm âncora**: declaram a limitação via `DataQualityNote` e oferecem apenas o retorno à evidência. Nenhuma seção foi criada para receber âncora.

**Testes:** `tests/brand-arrival-context.test.ts` — **31 casos** (válido; sem contexto; parcial; `ctx_from=gerencial` rejeitado; sinal desconhecido; marca incompatível; canal incompatível; parâmetro repetido; reader sem `getAll`; prioridade determinística; sem sinal ⇒ sem `ctx_*`; só identificadores/zero dígito; domínio de canal/marca; **as 8 combinações sinal × canal, incluindo TikTok rejeitando `ads_subutilizado`/`frete_alto`/`roas_forte` e aceitando `custo_alto`/`sem_dado`, mais o produtor não gerando incompatível**; **descrição neutra de `ads_subutilizado`**; mapa de evidência; todo enum com texto; retorno sem repropagar `ctx_*`; `ctx_*` fora de `FILTER_QUERY_KEYS`; null ≠ zero; produtor/consumidor únicos; zero fetch e zero modal novo; banner sem declarar frescor; âncora real com `scroll-mt`). Suíte web **460 passed**, typecheck e build verdes. **QA visual: pendente (Task 3).**

### 8.8. Task 3 — QA visual (06/08/2026): **PASS**. Gate G3 tecnicamente concluído

**Ambiente:** build de produção local em `localhost:3100` contra a API pública read-only (com G1+DQ2 já publicados); Playwright/Chromium **temporários e isolados em `%TEMP%`**; interceptação local usada **apenas** para devolver o header CORS da API pública (zero backend alternativo, zero escrita). Screenshots e logs fora do Git. **Viewports:** desktop 1440×900 e mobile 390×844 (tablet não foi necessário — nenhum finding de breakpoint).

**Jornadas executadas (10 verificações × 2 viewports) — zero finding de aplicação:**

| # | Jornada | Resultado |
|---|---|---|
| J1 | Acesso direto sem contexto | nenhum banner, nenhum espaço vazio, layout idêntico, URL sem `ctx_*` |
| J2 | Canais → detalhe → Marca | linha não-clicável; CTA só com `brands`, `channels`, datas, `compare` + os 4 `ctx_*`; **nenhum dígito nos valores de contexto**; banner **antes** dos KPIs, com canal e período coerentes e sem métrica |
| J3 | `ads_subutilizado` (evidência parcial) | descrição neutra, nota de escopo, CTA "Ver investimento do período" → `#marca-periodo`, seção visível e não encoberta, alvo ≥44px |
| J4 | TikTok + `custo_alto` | banner presente, **nenhuma âncora**, limitação de custo declarada, sem prometer ROAS/frete, retorno funcionando |
| J5 | TikTok + `roas_forte`/`ads_subutilizado`/`frete_alto` | banner **não** renderiza; página normal; nenhuma mensagem falsa |
| J6 | `ctx_brand` ≠ rota, depois troca pelos pills | contexto ignorado; cabeçalho da marca da rota; pills **não** propagam `ctx_*`; banner desaparece |
| J7 | Canal do contexto sai do filtro | com Shopee ainda selecionado o banner **permanece** (correto — o filtro é multi-seleção); ao **remover** Shopee o banner **desaparece** |
| J8 | Retorno à evidência | destino `/canais` com marca/canal/datas/`compare` preservados, **sem** `ctx_*`, diálogo não abre sozinho |
| J9 | Sidebar e URL compartilhável | sidebar **não** propaga `ctx_*`; a URL quente reexibe o banner |
| A11y | região nomeada, nomes acessíveis, alvos ≥44px, tabulação, âncora por teclado, headings | OK |

**Console: 0 erros · 0 hydration · 0 host inesperado · 0 overflow horizontal** nos dois viewports. **Estados:** sob falha total da API o banner permanece (o contexto vem da URL) e **não** declara frescor nem fabrica métrica — confirmado nos dois viewports.

**Nenhuma rodada consolidada de correção de aplicação foi necessária.** Os findings dos dois primeiros runs eram do **instrumento de teste**, corrigidos no script: (a) `waitUntil: "networkidle"` nunca estabiliza na página de Marca; (b) sem `networkidle`, J2 precisava aguardar a matriz de Canais renderizar; (c) J7 assumia seleção única de canal, quando o filtro é **multi-seleção com toggle**; (d) no mobile a sidebar é `hidden md:flex` (o menu vive no drawer).

**Dívida descoberta e registrada (fora do escopo, pré-existente ao G3):** `GET /api/v1/performance/brand-detail` **não responde em produção** — timeout em 120s e 45s para kokeshi mai/jun/jul e apice mai, enquanto `/performance/daily` responde em ~0,4s. A página de Marca já chamava esse endpoint antes deste gate (o G3 não adicionou fetch algum); o efeito é a seção "TikTok Shop — Inteligência (competência mensal)" não completar, enquanto KPIs, gráfico, últimos 7 dias e o banner de chegada funcionam normalmente. Corrigir exige backend, proibido neste gate.

**Dívidas preservadas:** propagação transitiva desde a Gerencial (`ctx_from=gerencial`); ausência de evidência de custo/frete/cancelamento/ROAS na Marca; `frete_alto` com risco de degeneração (herdado do DQ2).

**Validações finais:** web **460 testes**, typecheck e build verdes; `git diff --check` OK; scan de secrets/PII limpo; `package-lock.json` sem diff; zero dependência nova; zero arquivo de backend/API/pipeline/banco; **um único shell de diálogo** (`KpiDrilldownDialog`; o outro `role="dialog"` é o `MobileDrawer` de navegação, pré-existente).

**Gate G3 encerrado e versionado no commit `74cc3b1`. Nenhum deploy manual foi executado naquele gate.**

## 8.9. Gate G4 — Task 1: diagnóstico do timeout de `/brand-detail` (06/08/2026, read-only)

Diagnóstico da dívida levantada em §8.8. **Zero código, SQL, banco, deploy ou escrita.**

**Veredito: causa raiz confirmada — não é consulta lenta, plano, índice, view nem cold start. É ausência de conectividade Render → Data Mart (RDS).**

**Matriz HTTP (1 chamada por combinação, produção):** `brand-detail` deu **timeout em 4/4** combinações (kokeshi e apice × mai/2026 e jul/2026), sempre **0 bytes recebidos** aos 40s — determinístico, independente de marca e de mês. Controle `/daily` no mesmo período: **HTTP 200 em 0,82s**.

**Caminho real:** `router /brand-detail` → `gold_service.get_brand_detail` → **5 consultas sequenciais** via `_query`, sem retry, sem cache e sem timeout interno. Fontes: `monthly` e `daily` em `gold.tiktok_brand_daily`; `creators` em `gold.tiktok_creator_daily` (GROUP BY + LIMIT 5); `products` em `gold.tiktok_product_daily` (GROUP BY + LIMIT 5); `channel_funnel` na view `gold.v_channel_efficiency`. Todas as cinco casam com `_uses_datamart()` (prefixo `gold.`), então **cada uma abre uma conexão nova no `datamart_engine`** — o RDS AWS, que **exige VPN** (`runbook_sync_produtos.md`: "RDS AWS (VPN obrigatória)" vs "Neon (internet pública — sem VPN)"). `DECISIONS.md` lista "conectividade segura e read-only entre Render e RDS" como critério de uma decisão **futura**, isto é, ainda inexistente.

**Prova por separação de grupos (mesmo instante, produção):**

| Grupo | Rotas | Resultado |
|---|---|---|
| `gold_service` → `datamart_engine` (RDS/VPN) | `/brand-detail`, `/tempo-real`, `/inteligencia`, `/operacoes` | **4/4 timeout**, 0 bytes |
| `performance_service` → Neon (`marts.*`) | `/daily`, `/quality`, `/pedidos` (e todas as demais) | **200 em 0,42–0,82s** |

A separação é perfeita e **descarta cold start do Render** (rotas do Neon respondem em sub-segundo no mesmo momento). Todo o resto da API já havia migrado para `performance_service`; sobraram exatamente 4 rotas no serviço legado.

**Isolamento das 5 consultas (Task C) — com acesso ao Data Mart, `statement_timeout=20s`, uma execução cada (kokeshi 2026-05):**

| Consulta | Fonte | Status | Duração | Linhas |
|---|---|---|---:|---:|
| monthly | `gold.tiktok_brand_daily` | OK | 2.243,7 ms | 1 |
| daily | `gold.tiktok_brand_daily` | OK | 423,9 ms | 31 |
| creators | `gold.tiktok_creator_daily` | OK | 445,3 ms | 5 |
| products | `gold.tiktok_product_daily` | OK | 481,1 ms | 5 |
| channel_funnel | `gold.v_channel_efficiency` | OK | 473,0 ms | 3 |
| **total do serviço** | — | OK | **4.067,9 ms** | — |

Nenhuma consulta estourou o timeout; a primeira concentra o handshake. **Logo o SQL não é o problema.**

**Task D — `EXPLAIN`/índices: não executado, por não existir consulta problemática.** A instrução restringia o plano à consulta lenta e nenhuma se qualificou. **Decomposição do tempo em produção:** tempo de conexão = **100%** (TCP sem resposta, 0 bytes até 40–120s); tempo de consulta = **0** (nenhuma chega a executar); serialização = irrelevante (payload de 1+31+5+5+3 linhas); cold start = **descartado**.

**Contrato e necessidade (Task E):** o payload alimenta **apenas** a seção "TikTok Shop — Inteligência (competência mensal)" — `monthly` nos cards, `daily` no `ChannelMixChart`, `channel_funnel` no "Funil por Canal", `top_creators` e `top_produtos` nos Top 5. O frontend **já degrada essa seção isoladamente** (`{!detailLoading && !d}` → "Dados mensais indisponíveis — API offline"); KPIs, gráfico do período, Últimos 7 Dias e o banner do G3 seguem funcionando. No backend, porém, a resposta é atômica: as 5 são sequenciais e qualquer falha derruba o endpoint inteiro. O custo real hoje para o usuário é **esperar 45–120s antes de ver a indisponibilidade**.

**Menor correção recomendada (uma só, para a Task 2): fazer o caminho do Data Mart falhar rápido** — `connect_timeout` curto e explícito no `datamart_engine` (o `_query` já levanta erro tratado quando o engine é `None`), de modo que os 45–120s de espera virem uma falha em poucos segundos, que o frontend **já** sabe representar. Não remove funcionalidade (onde há VPN, continua funcionando), não cria endpoint, não toca SQL/índice/banco.

**Não recomendado agora, com evidência:** migrar `/brand-detail` para o Neon exigiria criar/sincronizar três fontes inexistentes lá — no `marts` só existe `fact_tiktok_product_daily`, sem equivalentes de `tiktok_brand_daily`, `tiktok_creator_daily` ou `v_channel_efficiency`. É frente de dados própria, não correção mínima.

**Achados classificados:** *bloqueador* — 4 rotas servidas pelo Data Mart inalcançável em produção (`/brand-detail`, `/tempo-real`, `/inteligencia`, `/operacoes`), afetando as telas correspondentes; *necessário* — falhar rápido em vez de pendurar; *dívida* — `_query` abre uma conexão nova por consulta (5 por request), ineficiência real mas não a causa; a migração das 4 rotas para uma camada de serving alcançável (decisão já prevista em `DECISIONS.md`); *fora do escopo* — VPN/VPC no Render, criação de índices, refactor do `gold_service`.

## 8.10. Gate G4 — Task 2: fail-fast do Data Mart (06/08/2026) — **GO COM RESTRIÇÃO**

**Mitigação aplicada, não correção.** A causa raiz permanece: **o Render não tem conectividade com o Data Mart (RDS)**. Esta task apenas encurta a espera — **os dados não voltaram**.

**O que mudou:** `app/config.py` ganhou `datamart_connect_timeout_seconds: int = Field(default=10, ge=1, le=30)` (validação pelo próprio Pydantic, sem dependência nova) e `app/database.py` teve `_make_engine(url, connect_timeout=None)` estendido para aplicar `connect_args={"connect_timeout": N}` **somente quando o parâmetro é fornecido**. O `datamart_engine` passa o valor configurado; o **engine principal/Neon é criado exatamente como antes, sem `connect_args` novo**. Preservados: `pool_pre_ping=True` nos dois, retorno `None` para URL vazia, `SessionLocal`, `DataMartSessionLocal`, `check_connection` e `check_datamart_connection`. **Nenhum retry, fallback, cache ou conexão compartilhada** foi introduzido, e a falha continua propagando como falha — nunca é convertida em sucesso vazio.

**Nada foi tocado em:** `gold_service.py`, consultas SQL, routers, schemas, frontend, banco, `.env`, Render/VPC/VPN, pipelines/Airflow, dependências.

**Provas medidas:**
- Host inalcançável (`192.0.2.1`, TEST-NET-1 da RFC 5737 — sem depender de IP externo real) com timeout de 5s: **falhou em 4,99s**, **1 única tentativa** (sem retry) e **sem DSN/senha na mensagem**.
- Engine principal/Neon: `SELECT 1` **OK**, inalterado.
- Data Mart real via VPN com `connect_timeout=10`: `SELECT 1` **OK em 2,09s** — o timeout **não** impede conexão válida.
- Testes focais `tests/test_datamart_connect_timeout.py`: **14 casos** (URL vazia → `None`; Neon sem `connect_timeout`; Data Mart com o valor exato; `pool_pre_ping` nos dois; default 10; faixa 1–30 aceita e 0/-1/31/120 rejeitados; falha de criação não vaza DSN; mensagens de `check_*` sem DSN/senha; fontes e roteamento do `gold_service` intactos; ausência de mecânica de retry/backoff no código, ignorando comentários). Suíte completa da API: **449 passed**; `compileall` OK; import/startup da API OK.

**Restrição que permanece:** `/brand-detail`, `/tempo-real`, `/inteligencia` e `/operacoes` **continuam sem conteúdo do Data Mart em produção**.

Sobre o efeito da mitigação, para não haver ambiguidade: o código está **implementado e validado**, mas **ainda não está ativo em produção**. **Somente após a publicação do backend** essas rotas passarão a falhar em aproximadamente **10s**; **até essa publicação, produção continua podendo esperar 45–120s**. E **mesmo depois de publicada, os dados continuarão indisponíveis** — o que muda é apenas a rapidez com que a indisponibilidade aparece, algo que o frontend já representa.

A **correção definitiva depende da decisão de camada de serving** (`DECISIONS.md`); a migração/sincronização dessas fontes para o Neon deve ser tratada futuramente **junto da arquitetura do Airflow**, sem ampliar o G4 (lembrando que em `marts` só existe `fact_tiktok_product_daily`, sem equivalentes de `tiktok_brand_daily`, `tiktok_creator_daily` ou `v_channel_efficiency`).

## 9. Riscos e não-objetivos

- **Não-objetivo:** registry universal, refactor do shell, drill transacional (endpoint novo), mudanças em Produtos/Tempo Real/Inteligência/Operações, alterar semântica de nenhum sinal existente.
- **Risco 1:** o "diagnóstico humano" de Canais precisa nascer dos dados já carregados (sinais + mediana) — se soar genérico, reduzir a escopo de sinal explicado (sem frase-síntese) em vez de inventar heurística nova.
- **Risco 2:** substituição mecânica pode alterar classes visuais por acidente — QA da Task 3 cobre os 3 diálogos, não só Canais.
