# Revamp UI V2 — Auditoria comparativa e plano de ciclo

**Gate:** V2-0 (auditoria e desenho — **zero implementação**)
**Status:** **CONCLUÍDO — REVISADO, aguardando versionamento**
**Data:** 06/08/2026 (rodada de correção consolidada aplicada na mesma data)
**HEAD no início e no fim:** `b91874c` == `origin/main` (Gate G4 versionado)
**Resíduos preexistentes preservados:** `DESIGN.md` modificado e `docs/shopee_datamart_daily_jobs_handoff.md` não rastreado — **não** editados, stageados, commitados nem removidos neste gate.
**Airflow:** pausado por priorização; **fora do escopo** deste gate.
**Referência auditada:** `https://github.com/b2b-gogroup/torre_b2b`, clonada em diretório temporário fora deste repositório e **removida ao final**.

O blueprint da nova Gerencial (Tasks D e G) está em [GERENCIAL_V2_SPEC.md](GERENCIAL_V2_SPEC.md). Este documento cobre a auditoria (A, B, C), a viabilidade de dados (E), o sistema visual (F) e o roadmap (H).

---

## 1. Diagnóstico executivo

A Torre venceu a batalha difícil — **verdade do dado** — e está perdendo a fácil: **narrativa visual**.

Os gates U0–U6 e G1–G4 entregaram algo que a referência não tem: estados explícitos de loading/error/empty/partial/stale, proteção contra dado obsoleto por identidade de requisição (`requestKey`/`resolvedKey`), filtros compartilháveis por URL com precedência definida, acessibilidade real (foco, `aria-live`, alvos de 44px, um único shell de diálogo) e recusa sistemática em exibir número que a fonte não sustenta (`N/D` do TikTok, escopo regional, sinais explicados).

O que falta é **densidade e continuidade**. A Gerencial atual tem 4 KPIs, 1 gráfico de uma única métrica sem controles, 1 painel de canais com 3 barras, 1 painel de Pulso e 1 tabela. Cinco blocos para responder seis perguntas. A referência tem 40+ telas e uma Gerencial de 2.342 linhas que, apesar de acessibilidade praticamente inexistente, encadeia meta → KPI → funil → evolução → ranking → tabela numa leitura vertical contínua.

**A lacuna visual não é estética: é estrutural.** A coluna esquerda tem altura fixa (`height={260}` no gráfico) e a direita tem altura de conteúdo; o container usa `items-start`, que proíbe qualquer coordenação de altura. A referência resolve o mesmo layout sem nenhum truque — o card **é** o item de grade e a área do gráfico é `flex-1 min-h-[18rem]`. Detalhe em §4.

**Tese do V2:** manter integralmente nossas vantagens de verdade e acessibilidade, e importar da referência apenas o que aumenta densidade útil e continuidade narrativa — nunca o modelo de negócio B2B, nunca o padrão de interação inacessível, **e nunca uma comparabilidade que a fonte não sustenta**.

---

## 2. Task A — Auditoria da referência (torre_b2b)

### 2.1 Escopo e método

Clone superficial em diretório temporário. **57** arquivos `page.tsx`; **25** áreas em `app/dashboard/`. Leitura profunda de `app/dashboard/gerencial/gerencial-client.tsx` (2.342 linhas) e de `app/dashboard/canais/canais-client.tsx` (2.060 linhas), mais inventário de rotas, componentes, `tailwind.config.ts`, `package.json` e contagens objetivas de acessibilidade em todo o repositório.

**Nota de confiança.** As afirmações desta seção marcadas **[V]** foram verificadas diretamente por mim (arquivo + linha + contagem reproduzível). As marcadas **[R]** vêm do inventário do agente auditor e têm evidência mais fraca — em particular a descrição das telas Financeiro, Produtos, Qualidade e Monitoramento de Preço, onde o relatório chegou a atribuir à referência um fato que é **nosso** ("margem real bloqueada, só ML tem ROAS/ACOS"). Nenhuma decisão deste plano depende de item **[R]**.

### 2.2 Inventário de rotas

| Área | Rotas | Pergunta de negócio | Aproveitável? |
|---|---|---|---|
| Gerencial | `gerencial/`, variante print, variante RCA | Desempenho comercial global | **Sim — estrutura** |
| Comercial/operacional | `canais/`, `produtos/`, `financeiro/`, `qualidade/`, `analise-carteira/`, `monitoramento-preco/`, `metas/`, `slow-moving/` | Receita/volume/preço por dimensão | **Parcial** |
| B2B puro | `vendedores/`, `clientes/`, `comissao/`, `carteira-inside-sales/`, `carteira-rca/`, `cadastro-rca-mercos/`, `salesops/`, `rd/`, CRM por CNPJ, conta corrente | Vendedor, cliente jurídico, comissão, crédito | **Não — sem equivalente** |
| ERP/integração | `mercos/*` (pedidos, estoque, vendas, títulos, conciliação) | Espelho de ERP | **Não** |
| Plataforma | `usuarios/`, `auditoria/`, `atividade/`, `agentes/`, `jornada/`, `relatorios/`, `torre-b2b/` | Administração, multi-torre | **Não** |

**[V]** As 8 rotas comerciais existem e têm client próprio (`produtos/produtos-client.tsx`, `monitoramento-preco/{client,preco-charts}.tsx`, `qualidade/{qualidade-client,evolucao-nps-chart}.tsx`, `canais/{canais-client,charts/}`, `financeiro/financeiro-client.tsx`, `analise-carteira/{client,components/}`, `metas/metas-client.tsx`).

### 2.3 Inventário de componentes por categoria

| Categoria | Componentes | Observação |
|---|---|---|
| Shell/navegação | `components/layout/{sidebar,header,mobile-sidebar-context,rca-mobile-nav}.tsx` | Sidebar **dark** (`#0f172a`) — conflita com nossa decisão de sidebar clara/lavanda |
| Filtros | `components/ui/multi-filter.tsx` + 7 dropdowns inline na Gerencial | Filtros são **inline por tela**, não um componente compartilhado |
| Cards/KPI | `components/ui/card.tsx`; `KpiCard` **inline** na Gerencial | KPI não é componente compartilhado na referência |
| Gráficos | `components/charts/brasil-map.tsx`, `gerencial/charts/evolucao-chart.tsx`, `canais/charts/canais-charts.tsx`, `qualidade/evolucao-nps-chart.tsx`, `monitoramento-preco/preco-charts.tsx` | recharts `^3.8.1` |
| Tabelas/rankings | `components/ui/table.tsx` + tabelas inline | Ordenação/expansão client-side por tela |
| Modais/drawers | `gerencial/{funil-drilldown,pedidos-modal,bonificacoes-drilldown,oportunidade-fechamento}.tsx`, `carteira/transfer-cliente-modal.tsx`, `clientes/cliente-modal.tsx` | **Um modal por assunto**, cada um reimplementando o shell |
| Estados | `components/ui/route-skeleton.tsx` + skeletons inline | Sem contrato de estado unificado |
| Exportação | deps `xlsx`, `jspdf`, `html2canvas`, `html-to-image`; rota `(print)/gerencial-print` | Export via captura de tela e XLSX |
| Primitivos | `ui/{badge,button,input,label,select,searchable-select,bulk-action-bar,separator}` | Radix UI |

### 2.4 Leitura profunda da Gerencial — o que sustenta a densidade

**[V]** Contagens em `gerencial-client.tsx`: 2.342 linhas · **7** `dynamic(` · **6** `useSWR` · **3** `sticky` · **0** `aria-` · **0** `role=`.

Ordem de leitura da página (evidência por linha):

| # | Bloco | Linha | Grid |
|---|---|---|---|
| 1 | Banner de erro sticky com "Tentar novamente" | ~814–830 | — |
| 2 | Sub-abas (Visão Geral / Matriz de Compras) | ~832–857 | — |
| 3 | Barra de filtros **sticky** (7 dimensões) | ~859–1180 | `flex flex-wrap` |
| 4 | **Meta do período** em faixa gradiente | 1423 | `grid-cols-1 md:grid-cols-[auto_1fr_auto] gap-6 md:gap-8 items-center` |
| 5 | Faixa de **5 KPIs** | 1471 | `grid-cols-2 lg:grid-cols-5 gap-3` |
| 6 | **Funil** de 4 etapas + totalizador | 1522 | `grid-cols-2 lg:grid-cols-4 gap-3` |
| 7 | **Evolução + ranking de marcas** | 1595 | `grid-cols-1 lg:grid-cols-5 gap-6` (3+2) |
| 8 | Metas por vendedor (tabela expansível) | ~1759+ | `grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-2.5` nos sub-blocos |
| 9 | Rankings em pares | 2029, 2180 | `grid-cols-1 lg:grid-cols-2 gap-6` |

**O bloco 7 é a peça decisiva desta auditoria** — é o análogo direto do nosso bloco com lacuna:

```
1595  <div className="grid grid-cols-1 lg:grid-cols-5 gap-6">           ← sem items-start
1596    <div className="lg:col-span-3 bg-white rounded-xl border ... flex flex-col">
                                       ↑ o CARD é o item de grade, não um wrapper
1663      <div className="p-4 flex-1 min-h-[18rem]">                    ← área do gráfico ABSORVE sobra
1664        {evolucaoLoading ? <div className="h-full ..."/> : <EvolucaoChart/>}
1677    <div className="lg:col-span-2 bg-white rounded-xl border ...">  ← UM card, não dois empilhados
```

Quatro decisões que, somadas, tornam a área vazia **impossível**: (1) nenhum `items-start`; (2) o card é o item de grade e portanto estica; (3) a área do gráfico é `flex-1` com piso `min-h-[18rem]`, absorvendo qualquer sobra; (4) a coluna direita é **um** card, eliminando aritmética de linhas implícitas.

Outros padrões de coordenação de altura **[V]**: em Canais, células de heatmap com `min-h-[56px]` (299–320), listas de ranking limitadas com `max-h-[280px] overflow-y-auto pr-1` (1586) e fallbacks de gráfico em altura declarada `h-[260px]` / `h-[220px]` (1576, 1617). Modal: `fixed inset-0 z-50 bg-black/50 backdrop-blur-sm flex items-stretch sm:items-center` + `w-full sm:max-w-6xl h-full sm:h-auto sm:max-h-[90vh] flex flex-col overflow-hidden` (485–487) — **tela cheia no mobile, diálogo centrado no desktop**.

Carregamento em camadas **[V/R]**: 7 `dynamic(ssr:false)` (mapa ~80KB, gráfico de evolução, 4 drill-downs, aba da matriz) e 6 `useSWR` em tiers, com a chave do tier seguinte dependendo do anterior (`evolucaoKey` nula até `chartsData` existir) — KPIs e funil pintam antes dos gráficos.

### 2.5 Contratos de backend da referência

**[R]** Quatro tiers: `/api/dashboard/all` (KPIs período atual + anterior + funil + alertas), `/api/dashboard/charts` (metas, marcas, ritmo), `/api/dashboard/evolucao` (série por granularidade **e por modo**), `/api/dashboard/rankings` (vendedores, clientes, produtos, UFs + UFs do período anterior), mais `/api/dimensoes` para popular filtros e drill-downs dedicados (`funil/pedidos`, `bonificacoes/pedidos`).

**Conclusão que importa:** a riqueza da referência **não é mérito de frontend**. Ela existe porque há endpoints desenhados para a tela — o período anterior vem calculado do backend, a granularidade é parâmetro, o ranking chega pronto com `participacao`, e o drill-down tem endpoint próprio paginado. Qualquer blueprint nosso que peça riqueza equivalente precisa dizer **de onde vem o dado**; é o que a Task E faz.

### 2.6 Acessibilidade da referência — achado decisivo

**[V]** Em todo o repositório (`app/` + `components/`): apenas **16** arquivos contêm qualquer `aria-`; **0** ocorrências de `role="dialog"`; **0** de `aria-live`; **0** de `aria-sort`. Na Gerencial: **0** `aria-` e **0** `role=`.

Consequência prática: os quatro drill-downs da referência são `<div>` sobrepostos sem papel de diálogo, sem rótulo acessível e sem contenção de foco; as colunas ordenáveis não anunciam estado; os skeletons não se anunciam. **Nada do padrão de interação da referência pode ser copiado como está.** Nosso `KpiDrilldownDialog` único, com foco contido, `Escape`, `inert` no shell e `aria-live`, é superior e permanece o alvo.

---

## 3. Task B — Auditoria da nossa Torre

### 3.1 Inventário das 11 rotas

| Rota | Pergunta | Filtros | KPIs / visualizações | Drill-down | Fonte | Estados | Problema de UI |
|---|---|---|---|---|---|---|---|
| `/` Gerencial | Estamos no esperado? Se não, por quê? | canal, marca, período, compare | 4 KPIs; TrendChart (só GMV); painel de canais (3 barras); Pulso; tabela por marca | KPI→diálogo; Pulso→diálogo; marca→`/brand/[brand]` | Neon | loading/error/empty/fresh + `aria-live` | **Lacuna vertical (§4)**; baixa densidade; gráfico sem controles |
| `/canais` | Qual canal cresce/é eficiente? | canal, marca, período, compare | KPIs por canal; matriz marca×canal | matriz→diálogo comparativo (mediana/p75, sinais explicados) | Neon | fresh/loading/error | Densidade OK; é hoje nossa melhor tela |
| `/produtos` | Quais SKUs concentram GMV? | abas por canal + Pareto/sinal/status | resumo Pareto; tabelas paginadas | — | Neon | identidade por canal (`produtos-request-key`) | Sem drill-down de linha; **escopo temporal difere por canal (§6.3)** |
| `/regioes` | Qual UF concentra GMV? | UF local + globais | mapa SVG lazy; tabelas UF/marca | UF filtra a própria página | Neon | **partial** (`null` × `[]`) | Cobertura parcial declarada (DQ2) |
| `/financeiro` | Onde ROAS/ACOS/custo estão críticos? | globais | KPIs de ads; tabelas com semáforo | — | Neon | fresh/loading/error | 3 tabelas com `TableScrollHint` |
| `/qualidade` | Qual marca tem cancelamento crítico? | globais | tabelas com semáforo | — | Neon | idem; TikTok `N/D` (DQ2) | Sem drill-down |
| `/pedidos` | Distribuição por status? | globais (sem compare) | barras por status | — | Neon (TikTok+ML) | Shopee isolada = sem cobertura, sem badge falso | — |
| `/tempo-real` | Como está hoje, por hora? | TikTok + dia corrente | acumulado horário | — | **Data Mart** | máquina de 5 estados | **Sem dado em produção** |
| `/inteligencia` | Que produto TikTok tem risco? | sem filtro global (design) | portfólio, Pareto, LTV | — | **Data Mart** | fresh/error | **Sem dado em produção** |
| `/operacoes` | Qual criador performa? | sem filtro global (design) | criadores, lives, alertas | — | **Data Mart** | fresh/error | **Sem dado em produção** |
| `/brand/[brand]` | Trajetória desta marca? | globais via querystring | KPIs diários; DailyChart; mix de canal | terminal; `BrandArrivalBanner` (G3) + volta à evidência | Neon (+ Data Mart na seção mensal) | **partial** | Seção TikTok mensal sem dado |

### 3.2 Ativos que o V2 deve preservar sem negociação

1. **Identidade de requisição** — `buildRequestKey(...)` + `resolvedKey`, com `display*` derivados ([page.tsx:39-43](../apps/web/app/page.tsx#L39-L43), [174-177](../apps/web/app/page.tsx#L174-L177)). Fecha até o frame entre a troca de filtro e o efeito.
2. **Filtros compartilháveis** — `FILTER_QUERY_KEYS = ["channels","brands","date_from","date_to","compare"]` e `mergeFilteredHref` com precedência do href explícito sobre o filtro global.
3. **Quatro primitives de drill-down (G2)** — `DrilldownContextLine`, `EvidenceRow`, `DataQualityNote`, `DrilldownCta`, compostos, sem registry.
4. **Um único shell de diálogo** — `KpiDrilldownDialog` para KPI e para Pulso; troca de view por estado, nunca modal empilhado.
5. **Contexto de chegada (G3)** — `ctx_*` allowlisted, fora de `FILTER_QUERY_KEYS`, sem número na URL, âncora só quando existe evidência real na página.
6. **Verdade da interface (DQ2)** — `N/D` + "Não disponível nesta fonte" em vez de zero; escopo regional declarado; sinais sempre com "por quê".
7. **Acessibilidade** — foco visível, alvos ≥44px, `aria-live`, `inert`, `Escape`, contenção de foco no drawer e no diálogo.

### 3.3 Fraquezas

- **Gerencial subaproveitada**: 1 gráfico, 1 métrica, 0 controles locais, 0 série de comparação, 0 matriz, 0 visão de movimento.
- **Nenhuma coordenação de altura entre cards vizinhos** em nenhuma tela — o defeito de §4 é sistêmico, apenas mais visível na Gerencial.
- **Zero `dynamic()` na Gerencial** (só Regiões usa, para o mapa); recharts entra no bundle inicial.
- **Sem componente de KPI com referência**: o delta existe, mas não a linha "vs. período anterior" no próprio card.
- **Alerta operacional hard-coded**: [page.tsx:376-387](../apps/web/app/page.tsx#L376-L387) procura literalmente a marca `lescent`. É regra de negócio embutida em JSX; deve virar item da fila de atenção alimentada pelo `executive-summary`.
- **Eixo Y perde precisão**: `tickFormatter` com `toFixed(0)` ([TrendChart.tsx:54-58](../apps/web/src/components/TrendChart.tsx#L54-L58)) exibe R$1,4M e R$1,6M como "R$1M" e "R$2M".
- **Duplicação de layout de tabela** entre `BrandTable`, `BrandPerformanceTable`, `MercadoLivreProductTable`, `TikTokProductTable`, `ShopeeProductTable`, `ProductTableShell`.

---

## 4. Task C — Diagnóstico técnico da lacuna visual

### 4.1 Estrutura atual do grid

[page.tsx:347-364](../apps/web/app/page.tsx#L347-L364):

```
347  <div className="grid grid-cols-1 lg:grid-cols-3 gap-4 items-start">
348    <div className="order-2 lg:order-none lg:col-span-2 lg:row-span-2">
349      <TrendChart ... />                       ← wrapper SEM altura; card interno SEM h-full
351    <div className="order-1 lg:order-none">    ← PulsoPeriodoPanel
361    <div className="order-3 lg:order-none">    ← ChannelPerformancePanel
```

Colocação em `lg`: o gráfico ocupa colunas 1–2 × linhas 1–2; Pulso cai em coluna 3/linha 1; Canal em coluna 3/linha 2. Duas linhas implícitas.

### 4.2 Altura de cada bloco

| Bloco | Composição | Altura |
|---|---|---|
| **TrendChart** | `p-5` (20+20) + cabeçalho (~20) + `mb-4` (16) + `ResponsiveContainer height={260}` | **≈ 338px — FIXA** ([TrendChart.tsx:29-43](../apps/web/src/components/TrendChart.tsx#L29-L43)) |
| **Pulso** | `p-4` + `gap-3`; título, resumo de saúde, até 3 botões `min-h-11` (44px cada), "Ver todos" (44), aviso de confiança (44) | **≈ 300–360px — de conteúdo** ([PulsoPeriodoPanel.tsx:71](../apps/web/src/components/PulsoPeriodoPanel.tsx#L71)) |
| **Canal** | `p-5` + `gap-4`; título + 3 linhas (rótulo + barra `h-2` + `p-1.5`) | **≈ 210–230px — de conteúdo** ([ChannelPerformancePanel.tsx:61](../apps/web/src/components/ChannelPerformancePanel.tsx#L61)) |

### 4.3 Por que a coluna esquerda termina antes da direita

A coluna direita soma **Pulso + `gap-4` + Canal ≈ 530–600px**. O gráfico é **≈338px**. Como o item que se estende por duas linhas não é mais alto que a soma das linhas, as **alturas das linhas passam a ser ditadas pela coluna 3**, e a área de grade do gráfico fica com ~530–600px.

Aí entra o defeito: **`items-start` significa `align-items: start`**. O item não é esticado até sua área de grade — ele mantém a altura de conteúdo. O resultado é uma faixa vazia de **~200–260px** sob o gráfico, dentro das colunas 1–2. É exatamente a área vazia observada.

**Ponto crítico:** trocar `items-start` por `items-stretch` **não resolve**. O item de grade é um `<div>` wrapper vazio; esticá-lo não estica o card branco lá dentro (que não tem `h-full`), e ainda que esticasse, `ResponsiveContainer height={260}` é pixel fixo. O wrapper cresceria invisivelmente e a faixa branca permaneceria. São **três** mudanças acopladas, não uma.

A assimetria é instável, não estática: com 1 canal filtrado e nenhum insight no Pulso, a coluna direita encurta e a faixa vazia migra para **baixo do Canal**. O defeito real é a **ausência de qualquer contrato de coordenação de altura**.

### 4.4 Comportamento por viewport

| Viewport | Layout | Lacuna? |
|---|---|---|
| Desktop ≥1024px | 3 colunas; gráfico 2×2 | **Sim**, ~200–260px sob o gráfico |
| Tablet 1024px | `lg` já ativo (Tailwind `lg` = `min-width:1024px`); colunas de ~314px, gráfico ~645px | **Sim, pior** — a coluna estreita faz o texto do Pulso quebrar mais, alongando a direita, enquanto o gráfico segue em 260px |
| Tablet <1024px | 1 coluna | Não |
| Mobile 390px | 1 coluna, ordem Pulso→Gráfico→Canal via `order-*` | Não |

Fragilidade adicional: `row-span-2` presume exatamente dois cards à direita. Um terceiro cairia na coluna 1, linha 3 — **abaixo** do gráfico, quebrando a intenção de layout.

### 4.5 Menor correção local possível

Três mudanças acopladas, ~6 linhas:

1. `page.tsx:347` — remover `items-start` (o padrão `stretch` passa a valer).
2. `page.tsx:348` — `lg:col-span-2 lg:row-span-2` **+ `flex`**, e o `TrendChart` recebe `className="h-full"`.
3. `TrendChart.tsx:29` — card `... p-5` + **`h-full flex flex-col`**; envolver o gráfico em `<div className="flex-1 min-h-[260px]">` e trocar `height={260}` por `height="100%"`.

É o padrão verificado da referência (§2.4). Elimina a faixa branca com risco baixo e zero mudança de dado.

### 4.6 Por que a correção local não basta

Ela remove o **espaço** vazio e cria um **problema de escala**: um gráfico de barras diárias esticado a ~600px de altura para acompanhar dois cards de texto tem proporção ruim e nenhuma informação nova. Preencher pixel não é preencher significado.

A causa raiz é de arquitetura da informação, e a página exibe quatro sintomas independentes:

- **Uma métrica, zero controles.** `TrendPoint` já entrega `date, label, gmv, orders` ([api-client.ts:356-361](../apps/web/src/lib/api-client.ts#L356-L361)) — `orders` é buscado e descartado. Não há seletor de métrica, de granularidade (vem imposta pelo backend em `tr.granularity`) nem de comparação.
- **`compare` é buscado e ignorado no gráfico.** O router não repassa `compare_period` para o serviço de tendência ([performance.py:130](../apps/api/app/routers/performance.py#L130)): o usuário liga a comparação, os KPIs comparam, e o gráfico não. Contradição visível.
- **Sem elo entre tendência, Pulso e canais.** Três blocos vizinhos e nenhuma interação entre eles: clicar num ponto do gráfico não filtra nada; um insight do Pulso não destaca seu ponto na série.
- **Ordem cognitiva incompleta.** A página vai de "o que aconteceu" (KPIs, tendência) direto para "quem" (tabela por marca), sem "onde/por quê" (matriz, movimentos) e sem "o que fazer" priorizado — o único alerta acionável é um `if (brand === "lescent")` hard-coded.

**Portanto:** a correção local entra no V2-1 como parte do sistema de alturas (§7), não como entrega isolada.

---

## 5. Matriz adotar / adaptar / rejeitar / superar

### Adotar

| Padrão | Evidência | Por quê |
|---|---|---|
| Card como item de grade + `flex-1 min-h-[Nrem]` na área de gráfico, sem `items-start` | ref. gerencial 1595–1677 | Solução direta e comprovada para §4 |
| Alturas declaradas para gráficos e fallbacks (`h-[260px]`, `h-[220px]`) | ref. canais 1576, 1617 | Skeleton e vazio ocupam o mesmo espaço do conteúdo — zero salto de layout |
| Listas de ranking com `max-h` + scroll interno | ref. canais 1586 | Coordena altura sem truncar informação |
| `min-h` uniforme em células de matriz/heatmap | ref. canais 299–320 | Grade legível e clicável |
| Faixa de 5 KPIs (`grid-cols-2 lg:grid-cols-5`) | ref. gerencial 1471 | Cabe a faixa final de KPIs (§6.1) sem apertar |
| Barra de filtros sticky | ref. gerencial 859+ | Filtro visível ao rolar página longa |
| Banner de erro com "Tentar novamente" que refaz todos os tiers | ref. gerencial 814–830 | Já temos por bloco; unificar o gesto |
| Carregamento em camadas com `dynamic(ssr:false)` para gráficos e drill-downs | ref. gerencial 20–50 (7 usos) | Densidade sem custo de bundle inicial |
| Modal em tela cheia no mobile, diálogo centrado no desktop | ref. canais 485–487 | Nosso diálogo já é acessível; falta esse comportamento responsivo |
| Escala de marca com 9–11 degraus + tokens semânticos | ref. `tailwind.config.ts:39-50` | Nossa escala tem 5 degraus e falta faixa média |

### Adaptar

| Padrão | Adaptação |
|---|---|
| Faixa "Meta do período" (`md:grid-cols-[auto_1fr_auto]`) | **Não temos meta.** Vira **faixa de confiança no dado** — horizontal e compacta, entre filtros e KPIs, clicável (§6.1). Mesmo peso visual, conteúdo honesto |
| Funil de 4 etapas | **Rejeitado como funil.** Vira **"Saúde do volume por canal"** (§6.2): métricas independentes por canal, cada uma na sua unidade, **sem** etapas monetárias exclusivas |
| Toggle de modo do gráfico (faturado/colocado) | Vira **seletor de métrica** (GMV / Pedidos). Granularidade é exibida, não controlada, no V2-1 |
| Ranking por vendedor com expansão | Vira **maiores altas/quedas por marca×canal**; a expansão inline é boa e ganha `aria-expanded` |
| Ranking de produtos | **Removido do V2-1** por escopo temporal incompatível entre canais (§6.3). Substituído por **"Concentração por marca"** |
| Tabela ordenável client-side | Já temos `SortableHeader`; falta `aria-sort` |
| Filtros inline por tela | Manter nosso hook central + URL; adotar apenas a compactação visual |

### Rejeitar

| Padrão | Motivo |
|---|---|
| Vendedores, comissão, bonificações, metas por vendedor, ritmo | Sem equivalente em B2C |
| Clientes jurídicos, CRM por CNPJ, conta corrente, limite, inadimplência, aging, títulos | Sem equivalente |
| Pipeline comercial, carteira inside-sales/RCA, salesops | Sem equivalente |
| Matriz de compras (SKU × cliente) | Sem cliente identificado |
| Multi-tenant de torres (`torre-switcher`) | Uma torre |
| Espelho de ERP (`mercos/*`) | Fora de escopo |
| Correção de atribuição de vendedor no drill-down (POST de override) | Escrita em produção; sem conceito e sem autorização |
| Sidebar dark `#0f172a` | Contraria decisão registrada (sidebar clara/lavanda) |
| Exportação por captura de tela (`html2canvas`/`jspdf`) e `xlsx` | `xlsx` foi **removida por vulnerabilidade alta** no U4; CSV puro é a nossa via |
| **Funil monetário de etapas exclusivas** | **Nossa fonte não sustenta.** Ver §6.2 |

### Superar

| Dimensão | Referência | Nosso alvo |
|---|---|---|
| Diálogos acessíveis | **0** `role="dialog"`, **0** foco contido | Um shell com papel, rótulo, `Escape`, foco contido, `inert` — **manter** |
| Regiões vivas | **0** `aria-live` no repositório | Anúncio de carga/erro por tela — **manter e estender** a estados parciais |
| Ordenação anunciada | **0** `aria-sort` | Adicionar `aria-sort` ao `SortableHeader` |
| Verdade do dado | Zeros no lugar de indisponibilidade | `N/D` + "Não disponível nesta fonte" + escopo declarado — **manter** |
| Dado obsoleto | `keepPreviousData` do SWR mostra dado antigo sob filtro novo | `requestKey`/`resolvedKey` descarta — **manter** |
| Filtro compartilhável | Estado local | URL como fonte de verdade com precedência definida — **manter** |
| Tooltip | `title=""` nativo, sem teclado | Tooltip acessível ou evidência no drill-down |
| Sinais | Chips mudos | Chip **sempre** com "por quê" (G2) — **manter** |
| Comparabilidade | Funil e totais que somam dimensões heterogêneas | Métrica só é comparada onde a definição é a mesma; caso contrário, lado a lado com unidade e escopo declarados |

---

## 6. Task E — Matriz de contratos de dados e viabilidade

### 6.1 Faixa final de KPIs (decisão de produto fechada)

**Cinco KPIs, nesta ordem:** `GMV` · `Pedidos` · `Ticket Médio` · `Investimento em Ads` · `ROAS por canal`.

**Confiança no dado NÃO é KPI.** Ela ocupa uma **faixa horizontal compacta entre o cabeçalho/filtros e a faixa de KPIs**, clicável, abrindo drill-down de cobertura, defasagem e avisos ativos.

| KPI | Fonte | Delta / referência | Regra |
|---|---|---|---|
| GMV | `/overview.gmv` | **Sim** — `prev_gmv`, `gmv_mom_pct` | Único com comparação garantida no contrato |
| Pedidos | `/overview.orders` | **Não** → "Comparação indisponível" | Preserva a ressalva de compradores (soma diária ≠ único no período) |
| Ticket Médio | `/overview.avg_ticket` | **Não** → "Comparação indisponível" | — |
| Investimento em Ads | `/overview.ad_spend` | **Não — sem delta** | Declara cobertura **ML + Shopee**; **TikTok `N/D`** |
| ROAS por canal | `/overview.ml_roas`, `.shopee_roas` | **Não — sem delta** | ML e Shopee **separados**; TikTok `N/D`. **Proibido** total consolidado, soma ou média simples dos ROAS. Se só um canal compatível estiver selecionado, destacar esse canal |

### 6.2 "Saúde do volume por canal" — o que o contrato realmente oferece

O funil monetário foi **removido**. O contrato **não** oferece valor monetário cancelado, valor monetário devolvido, GMV anterior às exclusões, nem etapas monetárias mutuamente exclusivas e comparáveis.

O que existe, verificado em `apps/api/app/schemas/performance.py:124-161`:

| Canal | GMV | Pedidos considerados | Cancelados (n) | Taxa de cancelamento | Devolvidos (n) | Taxa de devolução |
|---|---|---|---|---|---|---|
| **Mercado Livre** | `/overview.ml_gmv` | `ml_total_orders` (= não cancelados + cancelados) | `ml_cancelled_orders` | `ml_cancel_rate_pct` | **indisponível** | **indisponível** |
| **Shopee** | `/overview.shopee_gmv` (shop-stats é a fonte autoritativa) | `shopee_orders + shopee_canceled_orders` | `shopee_canceled_orders` | `shopee_cancel_rate_pct` | `shopee_returned_orders` | `shopee_return_rate_pct` |
| **TikTok Shop** | `/overview.tiktok_gmv` | `tiktok_orders` → exibir como **"Pedidos registrados"** (não inferir total considerado incluindo cancelados) | campo existe (`tiktok_canceled`) mas **valor é estruturalmente zero** (DQ1) | idem | idem (`tiktok_returned`) | idem |

**Rótulos (correção factual):** `ml_total_orders` **não** é "pedidos elegíveis" — é o **total considerado** (`ml_orders + ml_canceled`, [performance_service.py:1307](../apps/api/app/services/performance_service.py#L1307) e `:1321`). Em Shopee, `shopee_orders` é a população de **não cancelados**, e o total considerado é `shopee_orders + shopee_canceled_orders`. Portanto o bloco exibe **"Pedidos considerados" + cancelados + taxa**; "pedidos não cancelados" só é derivado (`ml_total_orders − ml_cancelled_orders`) se realmente necessário. No TikTok, o rótulo é **"Pedidos registrados"**.

**Regra do TikTok:** o campo existir não torna o valor verdadeiro. Cancelamento e devolução do TikTok são **`N/D` + "Não disponível nesta fonte"**, **nunca zero** — decisão já vigente no DQ2.

**Fórmula da taxa de cancelamento — uniforme entre ML e Shopee.** ML e Shopee usam **a mesma** definição: `cancelados / (não cancelados + cancelados)`. Verificado no serviço: `sh_canceled / (sh_orders + sh_canceled)` em [performance_service.py:918](../apps/api/app/services/performance_service.py#L918) e, em [:1341](../apps/api/app/services/performance_service.py#L1341), `sh_canceled / sh_total` com `sh_total = sh_orders + sh_canceled` declarado logo antes em [:1336](../apps/api/app/services/performance_service.py#L1336) — ou seja, **as duas expressões são idênticas**. Em ML, todas as definições de `ml_total` são `ml_orders + ml_canceled` ([:455](../apps/api/app/services/performance_service.py#L455), [:578](../apps/api/app/services/performance_service.py#L578), [:1307](../apps/api/app/services/performance_service.py#L1307), [:1479](../apps/api/app/services/performance_service.py#L1479), [:1531](../apps/api/app/services/performance_service.py#L1531)). Consequências normativas:

1. O frontend **exibe a taxa servida pelo contrato** e **não recalcula** nada.
2. O drill-down **declara a definição** da taxa em texto.
3. **Nenhuma taxa é recalculada usando outro denominador** — nem no cliente, nem por derivação.
4. **Não há ranking competitivo de cancelamento entre canais** na Gerencial. A justificativa **não** é aritmética: é que **fonte, processo de captura e semântica operacional dos status diferem entre marketplaces** (ML via API do marketplace, Shopee via export manual de `Order.all*.xlsx` com shop-stats como GMV autoritativo, TikTok com allowlist de status e maturação de 2–3 dias), e o **TikTok não tem cobertura confiável**. Portanto cada canal é apresentado **descritivamente**; a comparação válida é do canal contra si mesmo ao longo do tempo, ou contra a mediana/p75 **do próprio canal**.

### 6.3 Concentração: por marca, não por produto

Produtos **não têm escopo temporal uniforme**: Mercado Livre é ranking acumulado atual, TikTok é competência mensal, Shopee é competência mensal. Um "Top Produtos" sob o período global da Gerencial compararia três janelas diferentes sob o mesmo rótulo.

**Decisão:** ranking de produtos **removido** do V2-1; nenhuma comparação de produtos entre canais dentro do período global. A terceira coluna de "Movimentos" passa a ser **"Concentração por marca"**, servida por `/brands` no **mesmo período global** dos demais blocos: marca, GMV, participação no total selecionado, barra de share e concentração Top 1 / Top 3 quando houver base suficiente. O CTA para `/produtos` permanece, com texto deixando claro que **aquela tela tem contratos próprios por canal**.

Ranking de produtos na Gerencial fica registrado como **evolução futura dependente de contrato temporal uniforme** (§10, item 10).

### 6.4 Matriz completa por componente

| Componente | Métrica | Fonte | Grão | Endpoint | Classificação | Disponibilidade | Risco de falsa comparação |
|---|---|---|---|---|---|---|---|
| Faixa de confiança | cobertura, defasagem, nº de avisos | `/executive-summary.data_warnings` (+ `source`, `last_date`, `staleness_days`) | período | existente | **reuso** | todos | — |
| KPI GMV | GMV + referência anterior | `/overview` | período | existente | **reuso** | todos | — |
| KPI Pedidos | pedidos | `/overview` | período | existente | **reuso** | todos | sem delta (declarado) |
| KPI Ticket | GMV ÷ pedidos | `/overview` | período | existente | **reuso** | todos | sem delta (declarado) |
| KPI Investimento em Ads | ad_spend | `/overview` | período | existente | **reuso** | ML + Shopee; TikTok `N/D` | não somar canal sem ads |
| KPI ROAS por canal | ml_roas, shopee_roas | `/overview` | período | existente | **reuso** | ML + Shopee; TikTok `N/D` | **nunca** consolidar |
| Evolução temporal | GMV \| Pedidos por canal | `/trend` — **1 chamada por canal selecionado, até 3** (§6.5) | dia/mês | existente | **reuso, nº de chamadas maior** | todos | somar séries só no mesmo bucket e escopo |
| Série comparativa na evolução | período anterior | `/trend` passou a devolver `comparison` quando o filtro pede comparação (V2-2) | mesma do atual | **estendido, aditivo** | indisponível no V2-1 (declarado); **entregue na Task 1/2 do V2-2** | todos | alinhamento **ordinal**; total anterior só com todos os canais completos; ausência ≠ 0 |
| Granularidade selecionável | dia/semana/mês | `granularity=auto\|day\|week\|month` em `/trend` (V2-2); `auto` = regra antiga | — | **estendido, aditivo** | **entregue na Task 1/2 do V2-2** | todos | intervalo curto em grão mensal gera 1 ponto; semana ISO (segunda) com bordas parciais |
| Pulso do período | insights priorizados | `/executive-summary` | insight | existente | **reuso** | todos | risco comercial nunca misturado a aviso de dado |
| Resumo de canais | share de GMV | `/overview` | canal | existente | **reuso** | todos | — |
| **Saúde do volume por canal** | GMV, pedidos considerados, cancelados, taxa, devolvidos, taxa de devolução | `/overview` + `/quality` | período × canal | existente | **reuso** | ML sem devolução; TikTok `N/D` nas duas | **médio** — semântica de status difere por fonte; sem ranking entre canais (§6.2, itens 1–4) |
| Matriz Marca × Canal | GMV, share, variação, eficiência, sinais | `/brands` + `/canais` (mediana e p75) | marca × canal | existente | **reuso** | eficiência só onde há ads | comparar **dentro** do canal |
| Movimentos (altas/quedas) | variação absoluta e % | `/brands` (`total_gmv`, `total_gmv_prev`, `mom_pct`) | marca × canal | existente | **reuso** | todos | exigir piso absoluto e mostrar os dois números |
| **Concentração por marca** | GMV, share, Top 1 / Top 3 | `/brands` | marca | existente | **reuso** | todos | base insuficiente → não exibir concentração |
| Fila de atenção | alerta, impacto, evidência, confiança, ação, destino | `/executive-summary` (**já entrega todos os campos**) | insight | existente | **reuso** | todos | substitui o `if (brand === "lescent")` |
| Drill em ponto da série | dia clicado | `/overview` + `/brands` com período reduzido ao dia | dia | existente | **reuso** | todos | — |
| Bloco regional | GMV com cobertura regional | `/regioes/summary` | UF | existente | **reuso** | cobertura **parcial** | nunca comparar ao GMV total |
| Ranking de produtos | GMV por SKU | — | — | — | **fora do V2-1** (§6.3) | — | escopo temporal incompatível |
| Margem / CMV / devolução ML | — | — | — | — | **indisponível** | nenhum canal | não desenhar |
| Evidência pedido a pedido | — | — | — | — | **fora de escopo** | — | não existe grão servido |
| Blocos do Data Mart | — | `gold_service` | — | — | **indisponível** (G4) | — | não incorporar à Gerencial |

### 6.5 Contrato da tendência por canal

`/trend` devolve **uma série agregada por requisição, sem dimensão de canal no payload** ([api-client.ts:356-361](../apps/web/src/lib/api-client.ts#L356-L361)). Portanto:

1. O frontend faz **no máximo uma chamada de `/trend` por canal selecionado**: 1 canal → 1 chamada; 2 → 2; 3 → 3.
2. Cada chamada **reutiliza o endpoint existente** com **seleção unitária** de canal.
3. **Nenhum endpoint novo.**
4. **Nenhuma quarta chamada agregada** é necessária: o total por bucket é a **soma das séries numéricas** dos canais.
5. A soma por bucket **deve reconciliar** com o GMV/Pedidos agregado no mesmo escopo — o contrato de `/trend` já garante que a soma da série bate com `/overview` para a mesma cláusula de filtro.
6. **Canal sem dado permanece distinguível de canal com zero real**: ausência é `null`/`N/D`, zero é `0`.
7. A **identidade da requisição** inclui: **canais, marcas, período, métrica e retry**.
8. **Resposta obsoleta nunca aparece** depois da troca de filtro (`resolvedKey ≠ requestKey` ⇒ bloco não renderiza valor).
9. **Falha parcial:** as séries disponíveis permanecem; o canal ausente é **nomeado**; o total **não** é apresentado como completo; **nenhum fallback silencioso**.
10. Métricas disponíveis: **GMV e Pedidos** (`TrendPoint` já traz `orders`).
11. **Comparação com período anterior segue indisponível na série durante o V2-1** e é **declarada em texto** — sem controle visual morto.

### 6.6 Veredito da Task E — redação corrigida

A expressão "frontend-only" é imprecisa e foi substituída. O V2-1:

- **não cria endpoint** e **não altera backend, banco ou pipeline**;
- **reutiliza endpoints existentes**: `/overview`, `/brands`, `/trend`, `/canais`, `/executive-summary`, `/quality`, `/regioes/summary`;
- **poderá aumentar o número de chamadas, de forma controlada** — até **3** chamadas de `/trend` (uma por canal selecionado), somadas às respostas já usadas dos demais endpoints;
- **precisa coordenar frescor e falha parcial entre essas respostas** — a identidade de requisição passa a valer para o conjunto, não para uma chamada isolada;
- **não pode afirmar que um bloco está completo se uma fonte necessária falhar** — bloco parcial é rotulado como parcial, com o que faltou nomeado.

**Extensão aditiva (V2-2, opcional):** granularidade selecionável e série comparativa em `/trend`. Fonte de verdade existe, grão comprovado, regra definida (mesma cláusula de filtro, janela anterior), e nenhum dado fabricado. **Nenhuma read model nova em nenhum dos dois gates.**

---

## 7. Task F — Sistema visual e responsividade

### 7.1 Grid e coordenação de altura

| Viewport | Container | Grade principal |
|---|---|---|
| Desktop ≥1280px | `max-w-[1440px] mx-auto px-6` | 12 colunas, `gap-4` |
| Desktop 1024–1279px | `px-6` | 12 colunas, `gap-4` |
| Tablet 768–1023px | `px-4` | 6 colunas, `gap-3` |
| Mobile <768px | `px-4` | 1 coluna, `gap-3` |

**Regras normativas (nascem de §4):**

1. Nenhum container de grade com cards vizinhos usa `items-start`. Se um item precisar alinhar ao topo, usa `self-start` **nele**.
2. **O card é o item de grade.** Proibido `<div>` wrapper sem propósito entre a grade e o card.
3. Todo card em fileira com outros é `h-full flex flex-col`.
4. Todo gráfico fica em `flex-1 min-h-[Xpx]` com `ResponsiveContainer height="100%"`. **Nenhuma altura de gráfico em pixel fixo dentro de card em fileira.**
5. Skeleton, vazio e erro de um bloco usam a **mesma** altura mínima do conteúdo.
6. Lista que pode crescer usa `max-h-[Xpx] overflow-y-auto` + dica de rolagem.
7. **Proibido `row-span`** para empilhar cards em coluna: usar um `flex flex-col gap-4` como item de grade único.

### 7.2 Critérios mensuráveis de alinhamento (substituem o teto genérico de 96px)

O teto anterior era ambíguo e tratava área interna legítima de gráfico ou de empty state como defeito. Critérios corrigidos:

1. Em **desktop e tablet**, o final visual do card de **Evolução** e do item composto **Pulso + Canais** deve diferir em **no máximo 24px**.
2. **Nenhum `row-span`** pode criar linha órfã entre blocos de primeiro nível.
3. Nenhum espaço vertical entre blocos irmãos pode ultrapassar o **gap definido pelo grid**.
4. O conteúdo de um gráfico e seus eixos **não contam** como "área vazia".
5. Estados de **empty/error/loading** podem ter espaço interno intencional, desde que **centralizado** e com **altura limitada**.
6. No **mobile**, o empilhamento natural **não** está sujeito à regra de alinhamento inferior entre cards.
7. O QA **mede bounding boxes** dos cards; não estima visualmente.

### 7.3 Tipografia, espaçamento, densidade

| Papel | Classe |
|---|---|
| Título de página | `text-xl font-bold` |
| Título de seção | `text-sm font-semibold` |
| Valor de KPI | `text-2xl font-bold tabular-nums` |
| Valor secundário | `text-sm tabular-nums` |
| Rótulo de KPI | `text-[11px] font-semibold uppercase tracking-wide` |
| Corpo | `text-sm` · contexto `text-xs` · anotação `text-[10px]` |
| Cabeçalho de tabela | `text-[11px] font-semibold uppercase tracking-wide text-slate-500` |

Densidade: card `p-4`; `gap-4` entre seções, `gap-3` dentro; `rounded-2xl` em cards, `rounded-xl` em sub-blocos, `rounded-lg` em controles. **Todo número monetário/quantitativo usa `tabular-nums`.**

### 7.4 Cor semântica

Preserva a identidade lavanda (`brand-50/100/600/700/900`) com **adição de degraus médios** `brand-200/300/400/500` para hierarquia em barras e séries sem recorrer a `violet-*` genérico.

| Papel | Cor |
|---|---|
| Marca / seleção / foco | `brand-600` (`#7c3aed`) |
| Positivo | `emerald-*` |
| Atenção | `amber-*` |
| Crítico | `rose-*` |
| Neutro / indisponível | `slate-*` |
| Informativo | `cyan-*` |
| Canais | TikTok `violet-500` · ML `cyan-500` · Shopee `orange-500` (mantidos) |

**Regra:** cor nunca é o único portador de significado — sempre com ícone, sinal (`▲`/`▼`) ou rótulo. Indisponibilidade é **cinza neutro**, nunca vermelho.

### 7.5 Estados

| Estado | Tratamento |
|---|---|
| Skeleton | `animate-pulse` na altura final + `role="status" aria-busy="true"` |
| Vazio | Texto + próximo passo, centralizado, altura limitada |
| Erro | `bg-rose-50 border-rose-200` + "Tentar novamente" |
| Parcial | `DataQualityNote` nomeando **quais** fontes/seções faltam |
| Obsoleto | Bloco não renderiza dado antigo: `display*` protegido por `resolvedKey` |
| Indisponível na fonte | `N/D` + "Não disponível nesta fonte", cinza |

### 7.6 Interação, modais, sticky

Hover `hover:bg-violet-50/60`; foco `focus-visible:ring-2 focus-visible:ring-violet-500` (obrigatório em todo elemento interativo); selecionado com borda `brand-400` + `aria-pressed`/`aria-current`; alvo mínimo 44×44px.

Diálogo: mantém o shell único do G2 e **ganha** `w-full h-full` no mobile / `sm:max-w-3xl sm:max-h-[90vh]` no desktop, `flex flex-col overflow-hidden` com corpo rolável e cabeçalho fixo.

Sticky: topbar (existe); **novo** — barra de filtros compacta `sticky top-[altura da topbar] z-30` com `backdrop-blur`. Cabeçalho de tabela longa `sticky top-0` dentro do container rolável. Nada mais fica sticky.

### 7.7 Proteção contra excesso de UI

A densidade não pode virar documentação técnica visível:

- **fonte, grão e período precisam estar acessíveis** em contexto, tooltip ou drill-down;
- **não precisam ocupar texto permanente em todos os cards**;
- o rótulo permanente fica reservado a **limitação material** (ex.: `N/D` com motivo, escopo parcial, diferença de semântica entre fontes).

Não transformar a página em mosaico de cards iguais: a hierarquia vem de **um** bloco analítico dominante por dobra, cercado de blocos de apoio menores. Máximo de **9** blocos de primeiro nível. Card sem pergunta de negócio própria não entra.

---

## 8. Task H — Roadmap

| Gate | Escopo | Orçamento | Aceite |
|---|---|---|---|
| **V2-0** | Auditoria + blueprint (este documento + spec) | este único prompt + **1** rodada de correção consolidada (**consumida**) | §9 do enunciado original, revisado |
| **V2-1** | Reconstrução da Gerencial reutilizando endpoints existentes | implementação + **no máximo 1** correção consolidada | §8.1 |
| **V2-2** | **Task 1/2:** extensão aditiva de `/trend` (granularidade + série comparativa) — *tecnicamente concluída e versionada; backend `e8f0630` publicado no Render com smoke PASS, frontend versionado na Fase B* (§13.2). **Task 2/2:** propagação às outras superfícies — *não iniciada* | implementação + **no máximo 1** correção consolidada — **consumida** em 07/08/2026 (§13.1) | Task 1/2: §8.3. Task 2/2: regras de §7.1 em 11 rotas; sem regressão de contrato |
| **V2-3** | QA integrado (desktop/tablet/mobile + a11y) e produção | QA + **no máximo 1** correção consolidada — **consumida** | **`BLOCKED — REPLAN REQUIRED`**: as duas correções foram aprovadas, mas o sticky da própria Gerencial reprovou o gate (§15.3). Fechamento transferido ao **V2-4** (§16), sem V2-3.1 |
| **V2-4** | Correção terminal do sticky da Gerencial e fechamento do Revamp | gate terminal, **sem** rodada adicional | **`PASS`** (§16): barra ancorada em `top=0px` nos três viewports, regressões limpas. **Revamp V2 concluído e aprovado**, sem V2-4.1 |

Sem subgates recursivos (nada de V2-1.1). **Após duas correções do mesmo problema, parar e replanejar.**

### 8.1 Critérios mensuráveis para o V2-1

1. **Alinhamento:** diferença ≤**24px** entre o final visual do card de Evolução e do item composto Pulso+Canais, em desktop e tablet, com 1 e com 3 canais selecionados; zero linha órfã por `row-span`; nenhum espaço entre irmãos acima do gap do grid (medido por bounding box, §7.2).
2. Gerencial responde às 6 perguntas da narrativa, com **≤9** blocos de primeiro nível.
3. Evolução com **2 métricas** selecionáveis (GMV, Pedidos), granularidade **exibida** e comparação **declarada como indisponível** — sem controle morto.
4. Tendência por canal com **até 3 chamadas** de `/trend`, soma por bucket reconciliando com o agregado do mesmo escopo, canal sem dado distinguível de zero real.
5. **Falha parcial** tratada em todo bloco multi-fonte: séries/fontes disponíveis permanecem, o que faltou é nomeado, nenhum total apresentado como completo, nenhum fallback silencioso.
6. Faixa de KPIs exatamente **GMV, Pedidos, Ticket Médio, Investimento em Ads, ROAS por canal**; confiança **fora** dos KPIs, em faixa própria clicável.
7. **Delta somente em GMV.** Pedidos e Ticket exibem "Comparação indisponível"; Ads e ROAS **sem delta**; ROAS **sem** total consolidado, soma ou média.
8. "Saúde do volume por canal" com métricas independentes por canal, cada uma na sua unidade; rótulos **"Pedidos considerados"** (ML/Shopee) e **"Pedidos registrados"** (TikTok); **zero** representação monetária de cancelamento/devolução; **zero** segmento mutuamente exclusivo; devolução da Shopee como métrica independente, nunca partição do total; TikTok em `N/D` com motivo, nunca zero; taxa exibida como servida, **nunca recalculada com outro denominador**; **nenhum ranking competitivo** de cancelamento entre canais.
9. Concentração **por marca** via `/brands` no período global; **nenhum** ranking de produtos na Gerencial; CTA para `/produtos` declarando contratos próprios por canal.
10. Fila de atenção alimentada pelo `executive-summary` — **zero** regra de marca hard-coded no JSX.
11. Zero endpoint novo, zero dependência nova, zero escrita, zero alteração de backend/banco/pipeline.
12. Nenhum drill-down fora do shell único; nenhum modal empilhado; filtros preservados em 100% dos CTAs; `ctx_*` nunca em `FILTER_QUERY_KEYS`; identidade de requisição incluindo **canais, marcas, período, métrica e retry**.
13. Suíte, typecheck e build verdes; a11y sem regressão (foco, `aria-live`, 44px) e `aria-sort` onde houver ordenação.
14. Nenhum número exibido sem lastro: indisponível é `N/D` com motivo; fonte/grão/período acessíveis em contexto, tooltip ou drill-down (§7.7).

### 8.2 Mudanças a registrar para o DESIGN.md (não aplicadas)

`DESIGN.md` **não foi tocado** (tem alteração local preexistente e está fora do escopo). A aplicar quando autorizado: regras de coordenação de altura (§7.1); critérios de alinhamento de §7.2; degraus médios `brand-200/300/400/500`; densidade de card `p-4`; `tabular-nums` obrigatório; comportamento responsivo do diálogo; barra de filtros sticky; faixa de confiança como elemento próprio.

**Acrescentado após o V2-1:** a rampa tipográfica do `DESIGN.md` documenta 12/14/18/30px, mas a Torre usa há muito os passos de **anotação densa** `text-[11px]` e `text-[10px]` — presentes em **16** e **22** arquivos preexistentes, respectivamente, muito antes deste ciclo. O detector do Impeccable sinaliza esses dois valores como fora da rampa; a correção correta é documentá-los no `DESIGN.md` como passos `annotation` e `annotation-sm`, não removê-los do código. Também a escala do valor de KPI: o `DESIGN.md` exige 30px, e a faixa de cinco KPIs do V2 usa `text-2xl` (24px) para caber em tablet sem estourar a trilha — decisão do blueprint que precisa virar exceção documentada.

### 8.3 Critérios da Task 1/2 do V2-2 (extensão aditiva de `/trend`)

1. **Aditividade:** cliente que não envia `granularity` recebe o comportamento
   anterior; a compatibilidade é de **comportamento**, com schema **aditivo** (a
   resposta passa a trazer `comparison`, ainda que `null`, logo o JSON não é
   idêntico). `comparison` é opcional na resposta, e sua ausência com `compare=true`
   é lida como **indisponibilidade do contrato**, nunca como "não solicitada". Zero endpoint novo, tabela, read model, dependência ou
   pipeline.
2. **Allowlist fechada:** `auto|day|week|month`; qualquer outro valor devolve **422**.
   A string do usuário **nunca** é interpolada no SQL — a expressão sai de um mapa
   constante indexado pelo valor já validado.
3. **Uma implementação de SQL** para as duas janelas, parametrizada pelo período: o
   caminho atual e o comparativo não podem divergir por construção.
4. **`auto` preserva a regra anterior:** dia até 92 dias, mês acima. O campo
   `granularity` da resposta devolve o grão **efetivo**, e a interface informa qual
   foi quando o usuário pediu automático.
5. **Granularidade só na identidade das séries** (`buildChannelSeriesKey` e cache key
   de `fetchTrend`), nunca na chave global: trocar o grão refaz apenas `/trend`;
   trocar a métrica continua sem requisição; teto de **3 chamadas** preservado.
6. **Alinhamento ordinal** entre as duas janelas, com data e rótulo **reais** dos dois
   lados. Posição sem par permanece ausência, nunca zero; zero explícito permanece
   zero.
7. **Total anterior só com todos os canais comparativos completos.** Erro, vazio ou
   parcial tem estado próprio e **não apaga a série atual**.
8. **`compare=false` não renderiza nenhuma interface comparativa** — nem legenda, nem
   aviso, nem linha.
9. **Semana ISO (segunda a domingo)**, com a primeira e a última semana cortadas pelo
   período global e o corte declarado no drill-down; aritmética sem `Date`, imune a
   fuso.
10. **Uma** linha para o período anterior (o total), distinguida por traço e não por
    cor; a comparação por canal existe no dado e no drill-down, e não é desenhada.
11. Suíte de backend e de frontend, typecheck e build verdes; piso de 12px mantido
    nos arquivos tocados; zero arquivo das outras dez rotas alterado.
12. **Ordem de publicação:** deploy manual da API no Render **antes** do frontend que
    depende do contrato novo.
13. **Uma janela comparativa canônica** (`resolve_compare_period`), aplicada nos dois
    dependencies de filtros, com os **seis** endpoints agregados reportando o mesmo
    intervalo: mês fechado → mês anterior completo; customizado → janela deslizante
    de mesma duração; `compare=false` → nenhuma janela.
14. **Datas da comparação vindas do contrato HTTP**, transportadas até a interface e
    nunca reconstruídas dos buckets; janela desconhecida ou divergente entre canais
    bloqueia o total anterior e é nomeada, preservando a série atual; comparação
    vazia continua exibindo a janela real.
15. **Granularidades divergentes não se mesclam:** grãos distintos entre canais, ou
    grão explícito ignorado pela API, expõem estado próprio, sem merge, sem
    conversão e sem reagregação, nomeando canais e grãos.

---

## 9. O que explicitamente não deve ser copiado

Conceitos sem equivalente real na nossa operação B2C: **vendedores, clientes (jurídicos), comissão, bonificações, inadimplência, faturamento em aberto, pipeline comercial B2B, matriz de compras, multi-tenant de torres**, metas por vendedor, ritmo/projeção de fechamento, aging de títulos, limite de crédito, CRM por CNPJ, conta corrente, espelho de ERP.

Além dos conceitos, **quatro padrões técnicos**: (1) qualquer sobreposição que não seja diálogo acessível; (2) exportação por captura de tela ou via `xlsx` (removida por vulnerabilidade alta); (3) `keepPreviousData` exibindo dado de filtro anterior; (4) **funil de etapas monetárias exclusivas** — nossa fonte não sustenta (§6.2).

E **nenhuma linha de código** da referência é copiada: a transferência é de estrutura e decisão de produto.

---

## 10. Riscos e decisões abertas

| # | Risco / decisão | Severidade | Encaminhamento |
|---|---|---|---|
| 1 | Densidade maior pode pesar no bundle (recharts é síncrono na Gerencial) | Média | `dynamic(ssr:false)` para gráficos e conteúdos de drill-down no V2-1 |
| 2 | Até 3 chamadas de `/trend` aumentam latência e superfície de falha | **Média** | Falha parcial obrigatória (§6.5, item 9); séries carregam em paralelo; canal ausente nomeado |
| 3 | Coordenar frescor entre múltiplas respostas é mais difícil que numa só | **Média** | Identidade de requisição do conjunto, com métrica na chave (§6.5, item 7) |
| 4 | 4 rotas do Data Mart sem dado; a Gerencial **não** pode depender delas | Média | Blueprint usa exclusivamente Neon; decisão de serving fora deste ciclo |
| 5 | Extensão de `/trend` toca backend, que exige deploy manual (Render) | Média | V2-2, isolada e aditiva com default = comportamento atual |
| 6 | Parte do inventário da referência é `[R]` (evidência fraca) | Baixa | Nenhuma decisão depende de item `[R]` |
| 7 | Validação visual da referência não foi possível | Baixa | Ver §11 |
| 8 | Sem autenticação na Torre pública | Média (herdada) | Fora deste ciclo |
| 9 | **Semântica operacional dos status difere entre marketplaces** — fonte e processo de captura distintos (API × export manual × allowlist com maturação). A **fórmula** da taxa é a mesma em ML e Shopee (§6.2) | **Média** | V2-1 exibe a taxa servida, declara a definição, não recalcula com outro denominador e **não cria ranking competitivo entre canais**; apresentação descritiva por canal |
| 10 | Ranking de produtos na Gerencial | — | **Evolução futura**, dependente de contrato temporal uniforme entre canais (§6.3). Fora do V2-1 |
| 11 | ML sem devolução; TikTok sem cancelamento/devolução confiáveis | **Alta** | "Saúde do volume por canal" com `N/D` e motivo; nunca zero; nunca total consolidado |

**Nenhuma decisão de produto permanece aberta neste gate.** As duas que estavam pendentes foram fechadas: bloco dominante = **Evolução Temporal** (§Blueprint do spec) e faixa de KPIs = §6.1.

---

## 11. Validação executada neste gate

**Realizado:** prechecks de git (HEAD/origin/main/`b91874c`/stage vazio/resíduos); clone temporário da referência e **remoção** ao final; leitura direta de `page.tsx`, `TrendChart.tsx`, `PulsoPeriodoPanel.tsx`, `ChannelPerformancePanel.tsx`, `api-client.ts` (tipos), `routers/performance.py`, `schemas/performance.py`, `services/performance_service.py` (cálculo das taxas), `kpi_dictionary.md`, `tailwind.config.ts` de ambos os projetos; contagens objetivas de `dynamic`/`useSWR`/`aria-`/`role=`/`sticky` na referência; inventário de 27 endpoints; auditoria delegada das 11 rotas e da referência.

**Não realizado — e por quê:**

- **Nenhuma validação visual em navegador.** Este gate proíbe implementação. As alturas de §4.2 são **calculadas a partir das classes**, não medidas em runtime — a verificação numérica (§7.2) é tarefa do QA do V2-3.
- **A referência não foi executada.** Depende de Supabase e variáveis de ambiente ausentes; criar credenciais está fora de escopo. A análise é 100% de código, e está declarada como tal.
- **Nenhum teste, typecheck ou build rodado** — nenhum arquivo de código foi alterado neste gate nem na rodada de correção.
- **Nenhum banco, pipeline, Data Mart, Neon, Scheduler, Airflow ou deploy acionado.**

---

## 12. Registro da rodada consolidada do V2-1 (07/08/2026)

Nove findings de revisão estrita, todos corrigidos numa única rodada — detalhe em
[GERENCIAL_V2_SPEC.md](GERENCIAL_V2_SPEC.md) §15. Os três que alteram contratos
registrados neste plano:

1. **§6.4, faixa de confiança.** A linha da matriz de dados dizia "cobertura,
   defasagem, nº de avisos" a partir do `/executive-summary`. A cobertura passou
   a ser **disponibilidade de série**, derivada das chamadas de `/trend` já
   feitas — a existência de GMV nunca comprovou cobertura, e um zero real caía na
   mesma gaveta de uma ausência de linha. Defasagem e avisos continuam vindo do
   `/executive-summary`, agora com um estado explícito de "não verificado".
2. **§6.1, KPI de Investimento em Ads.** A nota de cobertura passou a derivar
   estritamente da seleção; as sete combinações úteis estão cobertas em teste.
   Ausência de valor continua `N/D` — o contrato não permite concluir que a
   ausência representa gasto zero.
3. **Modo demonstração.** Achado do QA, não da revisão: decidir demonstração só
   pelo `/overview` produzia KPIs mockados ao lado de matriz e evolução live.
   Passou a exigir que **todas** as fontes com fallback tenham caído para mock.

### 12.1 Veredito da tipografia — o que era dívida e o que não era

A afirmação anterior (§8.2) de que `text-[11px]`/`text-[10px]` no V2 eram "dívida
preexistente" estava **errada como justificativa**: a existência dessas classes em
arquivos antigos não transforma uma ocorrência nova em dívida herdada.

- **Introduzido pelo V2-1 e corrigido:** 26 ocorrências abaixo de 12px nos
  arquivos novos (`AttentionQueue` 8, `VolumeHealthCard` 9, `KpiBand` 7,
  `PulseChannelsColumn` 1, `GerencialDrilldowns` 1) mais uma no `EvolutionChart`.
  Todas elevadas para ≥12px. Um finding de `gray-on-color` na legenda de canal
  também foi corrigido: o hover do estado inativo passou a ser neutro, reservando
  o violeta ao estado ativo.
- **Dívida realmente preexistente, NÃO tocada:** 16 arquivos com `text-[11px]`,
  22 com `text-[10px]` e 3 com `text-[9px]`, fora dos arquivos do V2. Corrigi-los
  exigiria mexer nas outras dez rotas, o que este gate proíbe.
- **Pendência documental que permanece:** a rampa do `DESIGN.md` documenta
  12/14/18/30px e não tem passo de anotação densa; o valor de KPI do V2 usa
  `text-2xl` (24px) contra os 30px exigidos. Ambos seguem registrados para
  aplicação futura no `DESIGN.md`, que este gate não pode tocar.

Detector do Impeccable ao final da rodada: **zero findings** nos 12 arquivos
visuais do V2 (11 componentes + `app/page.tsx`).

### 12.2 Reparação de stop-loss (pré-commit)

Cinco inconsistências da própria rodada consolidada, corrigidas antes do commit —
registro completo em [GERENCIAL_V2_SPEC.md](GERENCIAL_V2_SPEC.md) §16. As duas que
alteram contrato deste plano:

1. **Decisão de modo demonstração** (§6.4 e §8.1, critério 5). A regra passou a um
   módulo puro que exige o **conjunto esperado** da requisição atual — quatro
   agregadas com fallback mais uma série por canal selecionado — e devolve também um
   estado `pending`, no qual uma fonte mockada fica em carregamento neutro em vez de
   exibir números. A heurística anterior confirmava demonstração com uma única fonte
   concluída, porque `every` sobre lista filtrada é vacuamente verdadeiro.
2. **Piso de legibilidade** (§7.3 e §12.1). A verificação de tipografia passou a
   cobrir estilo inline e CSS, não só classes Tailwind: os `tick={{ fontSize: 11 }}`
   do Recharts tinham escapado da varredura anterior. O QA agora mede o tamanho
   **renderizado** no navegador.

A afirmação de "12 caminhos de drill-down" foi substituída por **16 tipos de
acionamento**, com o critério de contagem explicitado — ver §15.6 do spec.

---

## 13. Registro da Task 1/2 do V2-2 (07/08/2026)

Duas linhas da matriz de §6 mudaram de status: **série comparativa** e
**granularidade selecionável** deixaram de ser "indisponível" e "extensão futura" e
passaram a implementadas. O que muda no plano, além da matriz:

- **O risco 5 da §10 materializou-se como previsto.** A extensão tocou backend, é
  aditiva e tem default igual ao comportamento atual — mas cria uma **ordem
  obrigatória de publicação**: API no Render antes do frontend. Publicar o frontend
  primeiro deixa o grão semanal sem efeito real. Nem backend nem frontend estão
  publicados.
- **O risco 2 (até 3 chamadas de `/trend`) não aumentou.** A granularidade entra
  apenas na identidade das séries, e a série comparativa vem **na mesma resposta** do
  período atual — o teto de 3 chamadas foi preservado, com um campo a mais em cada
  resposta em vez de uma requisição a mais.
- **O critério 3 da §8.1 foi superado por entrega, não revogado.** "Comparação
  declarada como indisponível — sem controle morto" era o aceite correto para o V2-1,
  quando o backend ignorava `compare_period` em `/trend`. Agora a comparação existe;
  a frase que a declarava indisponível foi removida do card, e o seletor de grão é um
  controle **vivo** de quatro estados.

Os critérios de aceite desta task estão em §8.3, e os contratos completos —
granularidade, comparação, alinhamento ordinal, semanas parciais e compatibilidade
retroativa — em §17 do [GERENCIAL_V2_SPEC.md](GERENCIAL_V2_SPEC.md).

**A propagação do design às outras dez rotas (Task 2/2) não foi iniciada:** nenhum
arquivo fora da Gerencial e de seus helpers foi alterado, e as fontes abaixo de 12px
das outras rotas seguem como dívida registrada.

### 13.1 Rodada consolidada de correção da Task 1/2 (07/08/2026)

Rodada **única** do V2-2, e com ela o orçamento de correção do gate está
**consumido**. Três findings materiais e cinco correções factuais.

**Bloqueador — a janela comparativa divergia dos KPIs.** É o risco 3 da §10
("coordenar frescor entre múltiplas respostas") manifestado num lugar que o
blueprint não previa: não no frescor, mas na **definição do período anterior**. Havia
duas regras no código — a janela deslizante de `filters.compare_period` e a correção
de mês-calendário aplicada localmente em overview/brands/quality. Com junho
selecionado, o KPI comparava com 01–31/05 e o gráfico novo com 02–31/05. Agora existe
uma função canônica na camada de período, e os seis endpoints agregados reportam o
mesmo intervalo. Detalhe em §17.7 do [GERENCIAL_V2_SPEC.md](GERENCIAL_V2_SPEC.md).

Vale registrar que o defeito **não foi criado** pela Task 1/2: `/canais` e
`/financeiro` já ecoavam a janela deslizante enquanto `/overview` usava o mês
fechado. O que a Task 1/2 fez foi tornar a divergência **visível**, ao colocar as
duas leituras no mesmo card.

**Alto — as datas reais da comparação eram descartadas.** O backend já devolvia
`comparison.date_from`/`date_to`, e o frontend reconstruía o intervalo a partir do
primeiro e do último bucket. Errado em três situações (semana parcial, mês parcial e
`data: []`). As datas passaram a ser transportadas do contrato até a interface, e
divergência de janela entre canais é agora um estado nomeado.

**Alto — granularidades diferentes não podem ser mescladas.** A regra "a mais grossa
vence" não tornava as séries compatíveis: apenas escondia o problema. O teste que a
legitimava foi **removido** e substituído por contraprovas de mismatch.

**Correções factuais.** "Compatibilidade byte a byte" virou "compatibilidade
retroativa de comportamento, com schema aditivo" — a resposta passa a incluir
`comparison`, ainda que `null`, então o JSON não é idêntico. E a ausência do campo
com `compare=true` passou a ser `unsupported` (indisponibilidade declarada), não
"não solicitada": o usuário pediu. O orçamento de correção volta a ser o do gate
inteiro, não um por task.

### 13.2 Publicação faseada da Task 1/2 (07/08/2026)

O risco 5 da §10 previa que a extensão de `/trend` exigiria deploy manual no Render.
A consequência prática foi tratada como **duas fases**, e não como uma entrega única,
porque o frontend novo depende de um contrato que só existe depois do deploy:

| Fase | Conteúdo | Estado |
| --- | --- | --- |
| **A** | 8 arquivos de backend | commit `e8f0630`, publicado manualmente no Render |
| **smoke** | validações HTTP read-only contra a API publicada | **PASS** — ver §17.9 do [GERENCIAL_V2_SPEC.md](GERENCIAL_V2_SPEC.md) |
| **B** | 11 arquivos de frontend + estes 3 documentos | versionado nesta entrega |

Confirmado com **dados reais** no smoke: grão semanal com semana ISO; janela
comparativa canônica (junho/2026 → 01–31/05, não a janela deslizante); a mesma janela
declarada por `/overview`; e a soma da série atual reconciliando com o `current.gmv`
do `/overview` em **R$ 0,00**.

**A publicação do frontend segue o fluxo automático GitHub→Vercel.** Esta entrega
para no push: o deployment **não** é declarado Ready, **não** foi consultado e **não**
passou por smoke visual em produção. A rodada consolidada de correção do V2-2
continua **consumida** (§13.1). A **Task 2/2** (propagação às outras dez superfícies)
e o **V2-3** ainda **não haviam sido iniciados naquela rodada** — foram concluídos depois (§15 e §16).

---

## 14. Task 2/2 do V2-2 — propagação às dez superfícies (07/08/2026)

**Aprovada e versionada no fechamento do Revamp V2 (10/08/2026).** *Ao ser escrita, esta seção registrava o estado daquela rodada: implementada, aguardando revisão.* Nenhuma alteração de contrato de dados,
métrica, filtro, endpoint ou regra de negócio: a task move linguagem visual,
hierarquia, densidade e estados, e nada mais.

### 14.1 O que foi unificado

Duas extrações, com **consumidores reais** (a regra de ≥2 consumidores foi
respeitada; nenhum framework, registry ou design system paralelo foi criado):

| Componente | Consumidores | Por que existe |
| --- | --- | --- |
| `layout/PageContainer` | **10** rotas | `max-w-7xl` (1280px) apertava tabelas largas em 1440; `py-8`/`gap-6` espalhavam os blocos. Passa a `max-w-[1440px]`, `px-4 sm:px-6`, `py-6`, `gap-3 sm:gap-4` — o ritmo já usado na Gerencial |
| `layout/PageHeader` | **9** rotas | ordem da narrativa (escopo antes dos filtros, sem `-mt-3`), barra de filtros **sticky** só onde há filtros globais, e título em `text-xl` |

`/brand/[brand]` recebe o container mas **não** o `PageHeader`: seu cabeçalho tem
composição própria (voltar para Canais, avatar da marca, pills de troca de marca)
e seria o único consumidor daquele contrato. Título alinhado a `text-xl` no
próprio arquivo.

**Barra sticky por rota**, conforme o contrato de filtros globais: sim em
`/canais`, `/regioes`, `/financeiro`, `/qualidade`, `/pedidos`; **não** em
`/produtos`, `/tempo-real`, `/inteligencia` e `/operacoes`, que não herdam
filtros globais — barra vazia ali seria afordância falsa. `/brand` mantém a
própria linha de filtros no fluxo, não fixa.

**Nível de título preservado em `<h2>`** de propósito: o `<h1>` da página é o do
shell (`Torre de Controle`). Promover para `<h1>` agravaria a dívida **U6-04**
(dois `<h1>`), que é do shell e está fora deste gate.

### 14.2 Progressive disclosure: nenhum drill-down novo

Decisão deliberada. O critério de §11.1 do [GERENCIAL_V2_SPEC.md](GERENCIAL_V2_SPEC.md)
exige que os dados **já carregados** sustentem contexto, evidência, limitação e
próximo passo útil. Nas nove rotas fora de `/canais`, nenhum bloco reúne os quatro
sem uma requisição nova — e requisição nova está proibida nesta task. O diálogo de
marca × canal de `/canais` permanece como a melhor superfície de detalhe e foi
preservado intacto, com um único `KpiDrilldownDialog` e os primitives do G2.
Criar modal decorativo seria pior que manter a navegação e o filtro que já
existem.

### 14.3 O que não mudou, por contrato

Request identity (`requestKey`/`resolvedKey`), estados loading/error/empty/partial/
fresh, `ctx_*` fora de `FILTER_QUERY_KEYS`, preservação de filtros nos CTAs, as
limitações declaradas de cada canal (N/D do TikTok em Qualidade, ausência de
cobertura Shopee em Pedidos, ROAS por canal sem consolidação, cobertura regional
distinta de GMV total, indisponibilidade explicada em Tempo Real / Inteligência /
Operações) e o conjunto de fontes de cada rota — agora **registrado em teste**,
de modo que acrescentar um fetcher a qualquer uma delas quebra a suíte.

**Checkpoint TikTok com frete: nada implementado.** Produção segue em `sub_total`;
`KPI_META` não afirma frete; a escolha entre `total_amount` e
`sub_total + shipping_fee` continua pendente; Produtos TikTok segue no valor de
produto sem rateio. Travado por teste.

### 14.4 Estado e limites desta entrega

Validações: **605 testes** (25 focais novos), typecheck e build verdes, detector do
Impeccable sem findings nos 12 arquivos visuais. `app/page.tsx` e
`src/components/gerencial/**` **não foram tocados** (diff vazio).

O issue cosmético dos rótulos semanais do eixo X no mobile de 390px, achado no
smoke da Task 1/2, **não foi corrigido** aqui — está reservado ao V2-3, junto com o
QA integrado. Esta task **não** passou por QA integrado nem por validação em
produção; o sanity visual executado cobre render, overflow e erro fatal, e nada
além disso. O **V2-3 não foi iniciado**.

---

## 15. Gate V2-3 — correção consolidada e QA integrado (10/08/2026)

> **Veredito: `BLOCKED — REPLAN REQUIRED`.** As duas correções deste gate foram
> **tecnicamente aprovadas** (§15.2), mas o QA encontrou um finding material na
> própria Gerencial (§15.3) que impediu o encerramento. **Nenhum código havia sido
> versionado** em nenhum momento. O fechamento foi transferido ao **Gate V2-4**
> (§16), sem criar V2-3.1. O veredito anterior de "PASS WITH ISSUE" está
> **revogado**: um sticky inoperante na tela principal não é ressalva cosmética.

### 15.1 Por que a Task 2/2 não foi aprovada isoladamente

A revisão da Task 2/2 encontrou um **finding material**: a barra de filtros
anunciada como sticky estava **estruturalmente inoperante**. `position: sticky` é
limitado pela caixa do elemento **pai**, e a barra vivia dentro de um
`<div className="flex flex-col gap-3">` do próprio `PageHeader`, que terminava
imediatamente depois dela — a barra "colava" apenas dentro da altura do cabeçalho
e rolava para fora da tela junto com ele. Medido em `/canais` antes da correção:
`scrollY=507` no desktop com o topo da barra em **−336px**; `scrollY=900` no mobile
com **−667px**.

O teste `P4` era insuficiente: verificava a presença da string `sticky top-0 z-30`,
não o comportamento. **Presença de classe CSS não é prova de sticky.**

O trabalho foi **absorvido pelo V2-3**, sem criar V2-2.1 nem qualquer subgate, e
esta rodada **consome a única correção consolidada do V2-3**.

### 15.2 As duas correções

**1. Sticky — `PageHeader` passa a devolver um Fragment.** Cabeçalho e barra saem
como **irmãos** no fluxo do `PageContainer`, cujo box tem a altura de todo o
conteúdo da página. Nenhum wrapper novo, nenhuma rota replica filtros, nenhum
`useGlobalFilters`/`requestKey`/fetch/querystring tocado. `PageContainer` continua
sem `overflow` e sem `transform` — os dois criariam bloco de contenção e matariam o
sticky de novo, e há teste para isso.

**2. Ticks do eixo X da Gerencial.** `interval = 0` (para ≤16 buckets) significa em
Recharts "renderize TODOS os rótulos" e faz `minTickGap` ser **ignorado**. Com o
grão semanal do V2-2 o rótulo ficou mais largo (`Sem. 29/06` ≈60px contra ≈34px de
`29/06`) e cinco deles não cabiam nos ~308px de área útil do mobile de 390px.
Agora: `interval="preserveStartEnd"`, `minTickGap={20}` e `margin.right` de 8→28 —
o Recharts derruba os rótulos do meio que não caibam, sempre preservando o primeiro
e o último, e a metade do último rótulo passa a ter espaço reservado. Fonte
permanece em **12px**: nada foi encolhido. Bucket, granularidade, comparação e
requisições intocados.

### 15.3 Finding material NÃO corrigido

**A barra sticky da própria Gerencial (`GerencialHeader`) tem o mesmo defeito
estrutural.** Medido nesta rodada: topo em **−729px** (desktop), **−597px**
(tablet) e **−631px** (mobile) após o scroll. É o mesmo padrão que o `PageHeader`
tinha, e vem do **V2-1** — não foi introduzido aqui.

Não foi corrigido porque o gate autoriza tocar a Gerencial **apenas** nos ticks
semanais. Fica registrado como pendência que precisa de decisão própria: a correção
é a mesma (devolver Fragment), mas exige autorização explícita para alterar
`components/gerencial/**`.

### 15.4 QA integrado

3 viewports × 11 rotas. **Sticky provado por medição** — `top=0px` após o scroll em
`/canais`, `/regioes`, `/financeiro`, `/qualidade` e `/pedidos`, contra −336px antes
— e **ausência** de barra confirmada em `/produtos`, `/tempo-real`,
`/inteligencia`, `/operacoes` e `/brand/kokeshi`. Ticks semanais sem colisão e sem
corte nos três viewports. Diálogo com shell único, `aria-modal`, foco contido,
Escape e devolução de foco ao gatilho. Estado parcial verificado com falha
**induzida** de uma fonte: as frescas permanecem, a ausente é nomeada, nenhum
skeleton preso.

**Limite de ambiente declarado:** o build local não alcança dado real — com a API em
`localhost` há `ERR_CONNECTION_REFUSED`, e com a API pública o origin local está
fora do CORS. Onde dado real era necessário, o **harness** interceptou as requisições
e as repassou com cabeçalho de CORS. Isso é técnica de teste: nada no repositório, no
backend ou no build mudou por causa disso. **Nada foi validado em produção**, e nada
aqui afirma publicação.

---

## 16. Gate V2-4 — correção terminal do sticky da Gerencial (10/08/2026)

Gate **terminal e estreito**, sem rodada adicional de correção. Absorve o
fechamento do V2-3 sem criar V2-3.1.

### 16.1 A correção

Um único arquivo de produto: `src/components/gerencial/GerencialHeader.tsx`. O
componente passou a devolver um **Fragment**, de modo que cabeçalho e barra de
filtros são **irmãos** no fluxo do container da Gerencial
(`max-w-[1440px] … flex flex-col gap-4`, em `app/page.tsx`), cuja caixa abrange
todo o conteúdo da página. É a mesma solução estrutural validada no `PageHeader`
das outras dez rotas.

`app/page.tsx` **não precisou mudar**: o `GerencialHeader` já era filho direto
desse container — verificado por teste, que também garante a ausência de wrapper
intermediário e a ausência de `overflow`/`transform` no container (os dois
criariam bloco de contenção e matariam o sticky de novo).

Os dois componentes seguem **separados de propósito**: `GerencialHeader` tem `<h1>`
e subtítulo próprios, `PageHeader` tem `<h2>` e barra opcional. Um contrato
genérico esconderia essa diferença sem ganho.

Preservados e travados por teste: título, subtítulo, `periodLabel`, `refreshedAt`,
`LiveStatusBadge`, o estado "Atualizando dados…", os filtros via `children`,
`useGlobalFilters` como fonte de verdade na página, querystring, request identity,
`top-0`, `z-30` abaixo do diálogo, e **zero** fetch, estado, modal ou filtro novo.
`EvolutionChart` **não** foi tocado neste gate.

### 16.2 Medições

| Viewport | Antes (V2-3) | Depois (V2-4) |
| --- | --- | --- |
| desktop 1440×900 | **−729px** | **0px** (scrollY 900) |
| tablet 1024×768 | **−597px** | **0px** (scrollY 768) |
| mobile 390×844 | **−631px** | **0px** (scrollY 844) |

Em cada viewport: título da Gerencial fora da viewport, 9–11 elementos de conteúdo
visíveis abaixo da barra, barra ocupando 157–279px (17–33% da altura, abaixo do
teto de 40%), 20 controles focáveis dentro da barra fixa com o controle focado
visível e clicável, zero overflow horizontal, zero erro fatal. Diálogo acima da
barra (`z-index` 50 contra 30), `aria-modal`, foco contido, Escape fecha e o foco
retorna ao gatilho. Nenhuma chamada, filtro ou valor mudou — as mesmas seis fontes.

Regressão: `/canais` mantém `top=0px` em desktop e mobile; `/produtos` continua
**sem** barra sticky.

### 16.3 O que não foi medido neste gate

Os **ticks semanais no mobile** não puderam ser **re-medidos** aqui. O gráfico só
renderiza com série real, e o build local não a alcança: API em `localhost` dá
`ERR_CONNECTION_REFUSED`, a API pública recusa o origin local por CORS, e o proxy
do harness — que funcionara no V2-3 — passou a falhar em todas as fontes nesta
rodada (`curl` direto responde 200; o `route.fetch` do Playwright, não).

A evidência do V2-3 continua **aplicável**, porque `EvolutionChart` não foi
alterado no V2-4 e o teste `V24-5` trava os quatro parâmetros exatos que produzem o
comportamento (`interval="preserveStartEnd"`, `minTickGap={20}`,
`margin.right: 28`, `fontSize: 12`). Medido no V2-3, com dado real: mobile com 3
rótulos e folga de **+48px**, tablet 5 rótulos e **+29px**, desktop 5 e **+75px**,
zero colisão, zero corte, fonte 12px, tooltip com a data completa.

### 16.4 Estado do Revamp V2

Com o sticky da Gerencial aprovado nos **três** viewports, o **Revamp V2 está
tecnicamente concluído e APROVADO** — V2-0 a V2-4. O working tree acumulado do
V2-2 Task 2/2, do V2-3 e do V2-4 foi **versionado no fechamento de 10/08/2026**.
**Não houve V2-3.1 nem V2-4.1.** A publicação segue o fluxo automático
GitHub→Vercel e **ainda não foi validada**: nada aqui afirma deploy, Ready ou
produção verificada.

Dívidas **pré-existentes**, mantidas como tal e não resolvidas: alvos de toque de
21–32px em `MarketplaceFilter`/`BrandFilter`/`DateRangeFilter` (de U1/U3), o `fetch`
cru do helper `fetchDailyRange` em `/brand/[brand]`, os dois `<h1>` do shell
(U6-04) e o checkpoint do GMV TikTok com frete, que segue **não implementado**
(produção em `sub_total`).

---

## 17. Smoke pós-publicação do Revamp V2 — 10/08/2026

**Veredito: `PASS WITH ISSUE`.** Somente `GET` e navegação read-only; nenhuma
alteração em Vercel, Render, domínio, alias, variável ou CORS.

Produção **comportamentalmente consistente com o commit `04d0d17`**. Sem acesso
autenticado ao painel da Vercel, a correspondência ao SHA não é afirmada: a prova é
por sinais exclusivos deste release — sobretudo o sticky da Gerencial ancorado, que
antes de `04d0d17` media −729/−597/−631px.

Domínio canônico: `https://mktplace-gobeaute.vercel.app`.

### 17.1 O que foi comprovado

- **11 rotas** respondendo **HTTP 200**: `/`, `/canais`, `/produtos`, `/regioes`,
  `/financeiro`, `/qualidade`, `/tempo-real`, `/pedidos`, `/inteligencia`,
  `/operacoes`, `/brand/barbours`.
- Backend público: `/openapi.json` e `/api/v1/performance/health-datasource` em
  **200**.
- **Zero** fallback "Demonstração · API offline" nas fontes saudáveis — a Gerencial
  exibe `Dados ao vivo`.
- **Sticky da Gerencial em `top=0px` nos três viewports**, com o título saindo de
  cena e conteúdo analítico visível abaixo da barra:

  | Viewport | Altura da barra | % da viewport |
  | --- | --- | --- |
  | desktop 1440×900 | 117px | **13%** |
  | tablet 1024×768 | 195px | **25%** |
  | mobile 390×844 | 239px | **28%** |

- **Zero overflow horizontal** em todos os viewports e rotas verificadas.
- **Ticks semanais legíveis, sem corte e sem colisão**: 3 rótulos no mobile (folga
  +48px), 5 no tablet (+29px) e 5 no desktop (+75px), sempre em 12px.
- **Comparação anterior e tooltip validados**: janela canônica declarada
  (`Período anterior: 2026-06-01 a 2026-06-30` para julho/2026) e tooltip com a data
  completa do bucket anterior nos três viewports.
- **Diálogo aprovado**: shell único, `aria-modal`, acima do sticky (`z-index` 50
  contra 30), foco inicial dentro, focus trap, Escape fecha, foco retorna ao gatilho,
  e filtros preservados no destino do CTA.
- **Canais e Produtos sem regressão**: em Canais o cabeçalho não sobrepõe os filtros
  e a barra permanece ancorada ao rolar (desktop e mobile); em Produtos o
  `PageHeader` é estático, sem sticky indevido, com tabs, resumo, exportação e tabela
  utilizáveis.
- **Zero finding causado pelo release.**

### 17.2 Ressalva — três superfícies sem fonte em produção

`/api/v1/performance/tempo-real`, `/api/v1/performance/inteligencia` e
`/api/v1/performance/operacoes` respondem **500**, verificado fora do navegador. A
causa é a dependência do **Data Mart, inalcançável a partir do Render**. No browser o
erro aparece rotulado como CORS apenas porque a resposta 500 do FastAPI não carrega
`Access-Control-Allow-Origin`; o CORS em si está correto, já que `/overview` e
`/canais` respondem 200 do mesmo origin.

Explicitamente:

- **não é regressão do Revamp V2** — a condição precede todo este ciclo (Gate G4);
- as páginas **degradam honestamente**: estado de indisponibilidade nomeado, sem erro
  fatal e sem dado inventado;
- a Torre **permanece incompleta nessas três superfícies**;
- **corrigir CORS isoladamente não resolve**: o problema é ausência de fonte, não
  cabeçalho de resposta;
- a correção definitiva depende de uma **camada de serving acessível ao backend**;
- a direção recomendada é **materializar/sincronizar esses dados no Neon** por uma
  orquestração **server-side futura, possivelmente Airflow**;
- **nenhuma solução dessa arquitetura foi implementada neste fechamento.**

### 17.3 Dívidas não bloqueantes

- Um controle da barra de filtros **sem nome acessível**.
- Um controle da barra de filtros **abaixo do tamanho mínimo** de alvo.
- Correspondência ao SHA comprovada **comportamentalmente**, não pelo painel
  autenticado da Vercel.
- Decisão do **GMV TikTok com frete** ainda **pendente** (produção em `sub_total`).

### 17.4 Encerramento

**V2-0 a V2-4: concluídos, versionados, publicados e validados.** Não há
necessidade de **V2-5**. A frente **Revamp Visual V2 está encerrada**.
