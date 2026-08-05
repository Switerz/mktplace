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

## 8. Riscos e não-objetivos

- **Não-objetivo:** registry universal, refactor do shell, drill transacional (endpoint novo), mudanças em Produtos/Tempo Real/Inteligência/Operações, alterar semântica de nenhum sinal existente.
- **Risco 1:** o "diagnóstico humano" de Canais precisa nascer dos dados já carregados (sinais + mediana) — se soar genérico, reduzir a escopo de sinal explicado (sem frase-síntese) em vez de inventar heurística nova.
- **Risco 2:** substituição mecânica pode alterar classes visuais por acidente — QA da Task 3 cobre os 3 diálogos, não só Canais.
