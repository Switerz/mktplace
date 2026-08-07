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
| Série comparativa na evolução | período anterior | `/trend` **ignora** `compare_period` ([performance.py:130](../apps/api/app/routers/performance.py#L130)) | — | — | **indisponível no V2-1 — declarado, sem controle visual morto** | — | — |
| Granularidade selecionável | dia/semana/mês | backend decide e devolve em `granularity` | — | — | **extensão aditiva, V2-2** | — | intervalo curto em grão mensal gera 1 ponto |
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
| **V2-2** | Propagação às outras superfícies + extensão aditiva de `/trend` | implementação + **no máximo 1** correção consolidada | Regras de §7.1 em 11 rotas; sem regressão de contrato |
| **V2-3** | QA integrado (desktop/tablet/mobile + a11y) e produção | QA + **no máximo 1** correção consolidada | §7.2 medido por bounding box; 0 erro de console/hydration; a11y sem regressão |

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
