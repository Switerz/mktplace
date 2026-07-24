# Plano de Revamp de UI/UX — Torre de Marketplaces

**Gate:** U0 — Auditoria e especificação (somente leitura, sem implementação) — **ENCERRADO** nesta rodada de correção. Não haverá U0.1; a próxima etapa é o Gate U1.
**Data:** 24/07/2026 (auditoria original) — corrigido em 24/07/2026 (rodada única de correção, sem nova auditoria do torre_b2b e sem implementação).
**Referência externa:** `github.com/b2b-gogroup/torre_b2b` (privado, clonado shallow e read-only em diretório temporário fora do workspace, apenas para leitura de padrões de arquitetura — nenhum código foi copiado, nenhuma regra de negócio B2B é assumida como aplicável). Não foi reconsultado nesta rodada de correção.

**Decisões de produto já aprovadas (autoritativas, não sujeitas a nova discussão neste documento):**
1. Sidebar persistente no desktop e drawer no mobile — aprovado.
2. A sidebar será clara/lavanda, integrada à identidade GoBeauté — nunca charcoal/dark.
3. O conflito com o `DESIGN.md` (ver §9) não é bloqueador de produto — é apenas uma atualização documental a fazer dentro do próprio Gate U1.
4. A Gerencial é a primeira seção funcional do revamp.
5. O projeto segue seção por seção após a Gerencial, até cobrir toda a UI atual.
6. Drill-downs iniciais são analíticos e somente leitura.
7. Não será criado endpoint de pedidos individuais neste projeto sem nova decisão explícita.
8. Não inventar rotas, métricas ou dados.
9. Não copiar regras de negócio B2B sem equivalente real na Torre.

**Status do Gate U1 — Fundação visual e novo shell:** **CONCLUÍDO/APROVADO** (24/07/2026). Sidebar clara/lavanda persistente no desktop, drawer mobile e topbar compartilhados foram implementados em um shell único (`apps/web/src/components/shell/`), substituindo o antigo `AppNav` renderizado por página. Filtros globais, contratos de API e regras de negócio não foram alterados. Os dois findings de revisão em runtime (container ausente em Financeiro/Qualidade; foco escapando do drawer) foram corrigidos e validados. Detalhes completos no relatório do Gate U1 (fora deste documento — ver histórico da conversa/commit). Este documento não foi reescrito para refletir o U1; apenas este marcador de status foi adicionado.

**Status do Gate U2 — Gerencial completa e padrão de drill-down:** **CONCLUÍDO** (24/07/2026). A Gerencial (`apps/web/app/page.tsx`) foi reorganizada na ordem cabeçalho → filtros → resumo executivo → grade de KPIs → área analítica (tendência + novo painel de canais) → tabela por marca → alerta operacional, sem alterar nenhuma chamada de API/backend/pipeline/banco. Os 4 KPI cards (GMV, Pedidos, Ticket Médio, ROAS) agora abrem um único `KpiDrilldownDialog` reutilizável, com decomposição por canal/marca usando somente `OverviewData`/`BrandRow` já carregados (`src/lib/kpi-drilldown.ts`). Uma função pura (`mergeFilteredHref`, `src/lib/filters/nav-links.ts`) combina os filtros globais atuais com o href de destino (do resumo executivo, do painel de canais ou dos drill-downs), com precedência do destino explícito sobre o filtro atual. Decisões de UX: o diálogo reaproveita o padrão de focus trap/Escape já validado no `MobileDrawer` do U1, e usa `inert` no `#app-shell-root` (novo id no `AppShell`) para impedir simultaneidade entre drawer mobile e diálogo, sem introduzir empilhamento de modais. **Dívidas para os próximos gates:** cobertura automatizada de componentes React segue zero (node:test não suporta JSX; regressão de `KpiCard`/drill-downs foi validada só por typecheck+build, não por render); painel de canais não foi validado visualmente em navegador real nesta rodada (sem ferramenta de navegador disponível).

**Status do Gate U3 — Canais e Marcas:** **CONCLUÍDO** (24/07/2026), após uma rodada de implementação e uma rodada única de correção consolidada pré-commit. `apps/web/app/canais/page.tsx` foi reorganizado na ordem cabeçalho (título/subtítulo/badge/período, com `LiveStatusBadge`/`refreshedAt`/`mockLimitationNote` gated por dado fresco) → barra de filtros → navegação interna compacta (âncoras reais `#comparativo`/`#tiktok-shop`/`#mercado-livre`/`#shopee`) → resumos por canal (cada um agora com `<h2>`+explicação curta) → matriz "Comparativo entre Canais" (mantida em destaque logo após os resumos, com nova coluna "Detalhe" e botão com nome acessível por linha, sem tornar a linha clicável) → tabelas detalhadas (nomes de marca agora navegam para `/brand/[brand]` via `mergeFilteredHref`, sobrescrevendo `brands`/`channels` da linha) → placeholder Shopee/insights/legendas preservados. Mesmo padrão "Finding 2" de chave de requisição da Gerencial (Gate U2) foi replicado (`resolvedKey`/`requestKey` com `retryKey`), fechando o diálogo aberto a cada novo fetch. Novo drill-down marca × canal (`ChannelComparisonDialogContent`, reaproveitando `KpiDrilldownDialog` — nenhum novo shell de modal) mostra GMV/pedidos, Ads/GMV/ROAS/ACOS, custo marketplace/frete seller com referência de mediana/p75 do mesmo canal (`findChannelMedian`, `src/lib/canais-channel-metrics.ts` — nunca mistura canal nem inventa comparação quando a mediana não existe), sinais e `data_warning`, com destino "Abrir visão completa da marca" preservando datas/compare. Não faz fetch novo ao abrir.

`apps/web/app/brand/[brand]/page.tsx` ganhou o mesmo padrão de chave de requisição para a visão diária (Tendência + Últimos 7 Dias), independente da chave própria (`brand`+competência mensal) já existente da Inteligência TikTok mensal — troca de marca pelos pills agora nunca exibe dados da marca anterior durante o carregamento. Foi adicionado link "Voltar para Canais" preservando filtros. **Correção de fallback mock (Task 5):** o `DailyRow` de demonstração (`mock-daily.ts`) gera `orders` combinando os 3 canais juntos, sem separação por canal — antes disso ficar visível, `summarize()` (`src/lib/brand-daily-summary.ts`) sempre somava esse total combinado independentemente da seleção de canal, superestimando pedidos/ticket médio em modo demonstração com seleção parcial. Corrigido com um parâmetro `ordersReliable` (nova função pura `isOrdersReliable`: confiável ao vivo, ou em demonstração só quando os 3 canais estão selecionados) — nesse caso, Pedidos/Ticket Médio (KPI e tabela de últimos 7 dias) ficam explicitamente "N/D", com nota curta explicando a limitação; GMV continua filtrável normalmente por já ser calculado por canal.

**Rodada de correção consolidada pré-commit (24/07/2026) — 3 findings, únicos, sem novo escopo:**
1. **Canais exibia dados antigos sob filtros novos/erro.** `kpis`/`brands`/`channelRows`/`channelMedians` brutos alimentavam totais, sorts, cards, tabelas, insights e o diálogo mesmo quando `dataIsFresh !== true` (ex.: após uma requisição falhar, ou no frame de render entre a troca de filtro e o efeito rodar). Corrigido com quatro constantes protegidas (`displayKpis`/`displayBrands`/`displayChannelRows`/`displayChannelMedians`, todas `null`/`[]` quando não frescas) — todo cálculo, hook de ordenação, card, tabela e insight passou a usar exclusivamente essas versões; todo gate de skeleton que usava `loading` passou a usar `!dataIsFresh` (que já captura corretamente o frame de transição, mesmo quando `loading`/`error` locais ainda não foram resetados pelo efeito); o diálogo passou a abrir apenas com `dataIsFresh && detailRow != null`, com conteúdo/mediana também condicionados a `dataIsFresh`. Nenhum framework novo de estado foi criado.
2. **"Últimos 7 Dias" reutilizava GMV combinado do mock em seleção parcial de 2 canais.** O patch anterior (`chartData`) só zerava canais não selecionados no caso de exatamente 1 canal ativo; com 2 de 3 canais selecionados, `total_gmv` (e a tabela, que lia `daily` diretamente) continuava somando os 3 canais do mock. Corrigido com uma nova função pura `projectDailyRowsBySelection` (`src/lib/brand-daily-summary.ts`) que zera todo canal não selecionado e recalcula `total_gmv` exclusivamente pela soma dos selecionados — o gráfico de tendência e a tabela "Últimos 7 Dias" agora consomem a MESMA coleção projetada (`projectedDaily`), eliminando a divergência estrutural entre os dois. Em modo ao vivo o efeito é um no-op (API já filtra por marketplace). `isOrdersReliable`/`summarize` não foram tocados — 8 novos testes cobrem TikTok isolado, TikTok+Shopee, ML+Shopee, exclusão correta do total, reconciliação com os 3 canais, e uma checagem cruzada de que `projectDailyRowsBySelection` e `summarize` concordam no GMV total para a mesma seleção.
3. **Alvo de toque do botão "Detalhe" abaixo do mínimo confortável em mobile.** Corrigido com `inline-flex items-center justify-center min-h-11 min-w-11` (44×44px), preservando o rótulo de texto compacto (sem virar botão com chrome visual) e a linha não clicável.

**Achados classificados (após a rodada de correção):**
- **Necessário (corrigido no U3):** fallback mock de Pedidos/Ticket Médio da página de marca reaproveitava o total combinado dos 3 canais sob seleção parcial (rodada de implementação); Canais exibindo dados antigos sob filtro/erro novo, GMV combinado em "Últimos 7 Dias" sob seleção parcial de 2 canais, e alvo de toque do botão "Detalhe" (rodada de correção consolidada, ver acima) — todos corrigidos e validados nesta rodada.
- **Dívida (pré-existente, fora do escopo deste gate — não é regressão introduzida aqui):** o fallback mock de `fetchCanais` (`src/lib/api-client.ts`) ignora completamente o filtro de marca (`filters.brands`) — sempre retorna as 5 marcas mock, mesmo com marca(s) filtrada(s); o `mockLimitationNote` já existente avisa sobre essa limitação geral do modo demonstração.
- **Fora do escopo:** nenhum endpoint/rota/tabela/métrica/threshold novo; nenhuma regra de negócio alterada; U4 não iniciado; não foi aberto U3.1 (esta foi a única rodada de correção do U3).

**Dívidas remanescentes:** validação visual em navegador real não foi possível nesta rodada (nenhum Playwright/MCP de navegador conectado nesta sessão — não instalado, conforme instrução); cobertura de componente React segue zero (mesma dívida do U2, validada por typecheck+build+testes de lógica pura); na página Canais, após um erro definitivo os dados antigos deixam de aparecer (corrigido), mas os skeletons continuam visíveis, porque o gate usa `!dataIsFresh` (que é `true` tanto durante o carregamento quanto após um erro) em vez de um estado `error-only` sem skeleton — substituir o skeleton por um estado dedicado de erro fica para o Gate U6 (QA integrado). **Próximo gate: U4 — Produtos, Regiões e Financeiro.**

---

## 1. Objetivo do revamp

Elevar a UI/UX da Torre de Marketplaces (GoBeauté) de um conjunto de páginas funcionais, mas isoladas entre si, para um sistema de navegação coeso, com hierarquia de informação deliberada e um fluxo de investigação repetível:

> **resumo → desvio → clique → explicação → detalhe verificável**

O objetivo não é redesenhar visualmente do zero nem importar conceitos de negócio B2B (carteira de clientes, comissão de vendedor, matriz de compras corporativa). É estudar a *arquitetura de interface* do torre_b2b — navegação, densidade, filtros, drill-downs, estados — e aplicar seletivamente o que resolve problemas reais já observados na Torre atual.

A primeira entrega concreta (fora deste gate) será a nova **Gerencial**, com drill-downs analíticos e somente leitura.

## 2. Usuários principais e decisões que precisam tomar

| Persona | Decisão que a tela precisa suportar |
|---|---|
| Time comercial/gestão GoBeauté (usuário primário da Gerencial) | "GMV/pedidos estão dentro do esperado este mês? Se não, por quê, e qual marca/canal puxou o desvio?" |
| Time de canais/marketplace | "Qual canal (TikTok/ML/Shopee) está com melhor eficiência de aquisição (novos vs. recompra, ROAS, COS%) agora?" |
| Time de produto | "Quais produtos/SKUs concentram o GMV (Pareto) e quais têm sinal de alerta (ads sem venda, estoque parado)?" |
</br>Nenhuma persona hoje tem papel de "vendedor individual" ou "representante" — a Torre não tem conceito de carteira por usuário, diferente do torre_b2b (que tem Torre RCA por representante). Isso é uma diferença estrutural relevante: **não há necessidade de multi-tenant de navegação por perfil de vendedor**.

## 3. Inventário das rotas e telas atuais

Base: `apps/web/app/` (Next.js 15 App Router, React 19, Tailwind 3.4, Recharts 2.13; sem shadcn/ui, sem SWR/react-query, sem gerenciador de estado externo).

| Rota | Arquivo | Conteúdo principal |
|---|---|---|
| `/` (Gerencial) | `app/page.tsx` | Header próprio + status live/demo, filtros globais, `ExecutiveSummaryCard` (saúde/mudanças/riscos com severidade e link), KPI cards (GMV, Pedidos, Ticket, ROAS condicional), `BrandPerformanceTable` (sortable, com metas por canal e link para `/brand/[brand]`), `TrendChart`, um alerta operacional hardcoded (Lescent ML) |
| `/canais` | `app/canais/page.tsx` (994 linhas) | KPIs por canal (TikTok/ML/Shopee), 4 tabelas (comparativo entre canais, atribuição TikTok, perfil de compradores ML/Shopee), insights automáticos textuais |
| `/produtos` | `app/produtos/page.tsx` (439 linhas) | Abas por marketplace (ML/TikTok/Shopee), filtros de Pareto/sinal/status/velocidade, tabelas paginadas por canal, estado assíncrono isolado por canal (`async-channel-state.ts`) |
| `/financeiro` | `app/financeiro/page.tsx` (544 linhas) | KPIs de ads/custo/frete, tabela marca×canal com semáforos de cor (ROAS/ACOS/custo total/fee%) |
| `/qualidade` | `app/qualidade/page.tsx` (532 linhas) | KPIs de cancelamento/entrega/defeito/avaliação, tabela marca×canal |
| `/regioes` | `app/regioes/page.tsx` (414 linhas) | Mapa SVG do Brasil (27 UFs) já interativo, tabelas por UF e por marca, avisos de cobertura incompleta |
| `/tempo-real` | `app/tempo-real/page.tsx` (483 linhas) | Gráfico acumulado/hora, pills de marca, **sem integração com filtros globais** (por natureza, dados sempre "agora") |
| `/pedidos` | `app/pedidos/page.tsx` (431 linhas) | BarChart de status de pedido, KPIs, tabela marca×canal — **sem drill-down para pedido individual** |
| `/inteligencia` | `app/inteligencia/page.tsx` (824 linhas) | Oportunidades de produto (Pareto/sinal/velocidade), LTV por coorte, insights TikTok — **sem integração com filtros globais** |
| `/operacoes` | `app/operacoes/page.tsx` (805 linhas) | Criadores TikTok (trend + tabela), alertas operacionais — **sem integração com filtros globais** |
| `/brand/[brand]` | `app/brand/[brand]/page.tsx` (741 linhas) | Drill-down de marca: KPIs, `DailyChart`, `ChannelMixChart`, tabela por canal, preserva querystring de filtros ao voltar |

**Navegação atual:** barra horizontal fixa no topo (`AppNav.tsx`), **não sidebar**, agrupada em 4 seções (Cockpits, Pedidos, Inteligência, Operações). Em mobile, rola horizontalmente (`overflow-x-auto`) — **não existe drawer**.

**Filtros globais:** canal, marca, período (com presets) e comparação, centralizados no hook `useGlobalFilters` (`apps/web/src/hooks/useGlobalFilters.ts`), sincronizados com a querystring como fonte única de verdade. Preservados entre rotas "filter-aware" via `lib/filters/nav-links.ts`. Três rotas (Tempo Real, Inteligência, Operações) **não usam esse hook** — hoje isso é coerente com a natureza "sempre atual" delas, mas deve ser uma decisão explícita na nova arquitetura, não um esquecimento.

**Contratos de dados:** todos os tipos (~60 interfaces) vivem centralizados em `apps/web/src/lib/api-client.ts` (59 KB) — não há pasta `types/` separada (a pasta existe mas está vazia; código real fica em `src/`). Cache em memória de 5 min por chave de request.

**Testes:** 14 arquivos em `apps/web/tests/`, rodados via `node --test` (script `test` no `package.json`). Cobrem exclusivamente lógica pura (presets de data, parsing de filtros, formatação, sortable table, máquina de estado assíncrono, paths SVG do mapa). **Zero teste de componente React, zero E2E/Playwright.** Também não há script de lint (`.eslintrc`/`eslint.config` ausentes) nem de typecheck no `package.json` — só `dev`, `build`, `start`, `test`.

## 4. Resumo da arquitetura de interface do torre_b2b

Stack: Next.js 14 App Router, **shadcn/ui**, Recharts, Supabase, SWR.

- **Sidebar colapsível** (240px expandida / 56px colapsada, estado em `localStorage`) + **drawer mobile** (85vw, slide-in). Três "torres" navegáveis via `TorreSwitcher` (Performance/RCA/CRM), cada uma com tema de cor próprio.
- **`nav-config.tsx`**: grupos (`NavGroup[]`) com itens tipados — ícone (Lucide), descrição/tooltip, badge (`beta`/`novo`), gates de permissão (`adminOnly`, `strictAba`, `hardGate`, `allowEmails`), flag `external` para links de Google Sheets.
- **Header sticky** com ícone gradiente por seção, título/subtítulo, slot à direita para filtros/ações.
- **`MultiFilter`**: componente reutilizável de multi-seleção (busca opcional, modo chips ou checkbox, contador no botão, botão "limpar") — usado para marca, filial, vendedor etc. Padrão bem mais reutilizável que os filtros pontuais atuais da Torre (`MarketplaceFilter`, `BrandFilter` são componentes separados e não genéricos).
- **Dimensões carregadas server-side** (marcas, filiais) e passadas como props ao client component — evita waterfall de fetch.
- **Gerencial**: header → barra de filtros (presets de período, `MultiFilter`, botões TV/Imprimir/Exportar) → 5 KPI cards com delta → sub-abas ("Visão Geral" / "Matriz de Compras") → seções: gráfico de evolução (toggle Faturado/Pedido Colocado, granularidade dia/semana/mês, comparação MoM), ranking de marcas, funil de pedidos (clicável → modal), card de bonificações (clicável → modal), mapa do Brasil, metas com barra dupla + projeção linear, rankings paginados (vendedores/clientes/produtos) com exportação XLSX.
- **Drill-downs**: sempre **modal** (via `createPortal`), nunca drawer — tabela paginada + filtros internos (reaproveita `MultiFilter`) + ações em linha + exportação/impressão no rodapé. O total do modal sempre reconcilia exatamente com o KPI que o originou.
- **Lazy loading**: `next/dynamic({ ssr: false, loading: ... })` para mapa (80KB SVG) e gráficos Recharts (~250KB) — reduz bundle inicial em ~330KB. A Torre atual **não faz isso** — Recharts é importado diretamente em várias páginas.
- **Estados**: skeleton (`bg-slate-100 animate-pulse`), spinner (`Loader2`), `loading.tsx` de rota (Suspense do App Router) — um padrão que a Torre atual não usa (estados de loading são só client-side, sem Suspense de rota).
- **Matriz de Compras** (SKU × mês por cliente) e **Torre RCA/CRM** são conceitos de negócio B2B específicos, sem análogo direto.

## 5. Matriz comparativa

| Aspecto | Situação atual | Referência torre_b2b | Recomendação | Prioridade | Disponibilidade de dado |
|---|---|---|---|---|---|
| Navegação principal | Top nav horizontal, sem drawer mobile | Sidebar colapsível + drawer mobile | Sidebar clara/lavanda (nunca charcoal) + drawer mobile — **decisão de produto já aprovada**; implementação e atualização estreita do `DESIGN.md` ficam no U1 (ver §9) | Necessário (execução no U1) | N/A |
| Filtros globais | Hook único, sincronizado com URL, robusto | Estado local por página + SWR, menos consistente que o nosso | **Manter** o padrão atual de URL como fonte única — é mais robusto que a referência | Dívida (documentar, não mudar) | Dado já existe |
| Componente de filtro multi-seleção | `MarketplaceFilter`/`BrandFilter` ad-hoc, não genéricos | `MultiFilter` genérico e reutilizável | Extrair um `MultiFilter` genérico na Torre | Necessário | Dado já existe |
| Resumo executivo com explicação de desvio | `ExecutiveSummaryCard` já existe (saúde/mudanças/riscos com severidade + link) | Sem análogo direto — funil/bonificações abrem modal, não há "card de saúde" central | **Expandir** o `ExecutiveSummaryCard` existente como base do fluxo resumo→desvio→explicação (já é 70% do caminho) | Bloqueador (é o coração da nova Gerencial) | Dado já existe |
| Drill-down de KPI/linha de tabela | Só `/brand/[brand]` (navegação de página); nenhum modal | Modal com tabela paginada + filtros internos, reconciliando com o KPI | Adotar padrão de modal para drill-downs analíticos read-only | Necessário | Parcial — ver §8 |
| Drill-down a pedido individual | Não existe (só agregados por marca/canal/dia) | Existe (funil abre lista de pedidos individuais) | **Não será criado neste projeto sem nova decisão explícita** (decisão de produto já registrada) | Fora do escopo | Dado não localizado no contrato atual |
| Gráfico de evolução com múltiplos modos | `TrendChart`/`DailyChart` simples (uma série) | Toggle Faturado/Pedido Colocado, granularidade, MoM overlay | Avaliar 1-2 modos relevantes (ex: GMV vs. Pedidos) para a nova Gerencial | Dívida | Dado já existe (granularidade day/month já suportada) |
| Metas e atingimento | Já existe (`GoalAttainment`, colunas de meta na `BrandPerformanceTable`) | Card duplo + projeção linear de fechamento | Manter o que existe; projeção de fechamento é incremento de valor, não bloqueador | Dívida | Dado já existe (falta só a projeção, calculável no frontend) |
| Análise regional | Já existe (`RegioesBrazilMap`, mapa próprio, tabelas por UF) | Mapa de heatmap por UF, clique filtra | **Manter** — já equivalente ao padrão da referência | Fora do escopo (já entregue) | Dado já existe |
| Matriz de compras (SKU × mês × cliente) | Não existe | Existe, é o "Cockpit" mais específico de B2B | **Não transportar** o conceito B2B; se houver demanda futura, adaptar para "Produto × Mês × Marketplace" usando os endpoints de produtos já existentes | Fora do escopo | Dado parcialmente existente (via `fetchProdutosML/TikTok/Shopee`), mas não como pivô |
| Tabelas: paginação/ordenação/busca/exportação | Ordenação sim (`useSortableTable`); paginação só em Produtos; busca e exportação **não existem** | Todas as 4 capacidades presentes de forma consistente | Padronizar paginação+exportação XLSX como componente reutilizável | Necessário | Dado já existe nos endpoints paginados (`limit`/`offset`); exportação é só frontend |
| Estados de loading | Skeleton client-side (`Skeleton.tsx`), sem `loading.tsx` de rota | Skeleton + `loading.tsx` (Suspense de rota) | Adotar `loading.tsx` por rota para reduzir tela em branco | Dívida | N/A |
| Lazy loading de componentes pesados | Recharts/mapa carregados eagerly | `next/dynamic({ssr:false})` para mapa/gráficos | Adotar lazy loading nos componentes pesados (mapa, gráficos, drill-downs) | Necessário (mais telas = mais peso) | N/A |
| Permissões/roles na navegação | Não existe conceito de usuário/perfil | `adminOnly`/`strictAba`/`hardGate`/`allowEmails` | **Não aplicável agora** — não há auth/roles na Torre hoje | Fora do escopo | Dado não localizado (sem schema de usuário conhecido) |
| Testes de UI | Só lógica pura (`node --test`), zero componente/E2E | Não avaliado (fora do escopo desta auditoria) | Introduzir ao menos smoke tests de render + 1 fluxo Playwright por gate | Necessário | N/A |
| Lint/typecheck no CI local | Ausentes (`package.json` só tem `dev/build/start/test`) | N/A | Adicionar scripts `lint`/`typecheck` (`tsc --noEmit`) | Necessário | N/A |
| Identidade visual (GoBeauté) | `DESIGN.md` aprovado, sóbrio, violeta, sem sidebar dark | N/A (referência tem tema próprio) | Preservar `DESIGN.md` como fonte da verdade; atualizar a regra de sidebar (§9) **dentro do próprio U1**, não como pré-requisito bloqueante | Necessário (execução no U1) | N/A |

## 6. Arquitetura de informação proposta

Grupos de sidebar propostos (mapeando 1:1 as seções já existentes na `AppNav` atual, sem inventar rotas novas):

```
Cockpits
  ├─ Gerencial        (/)
  ├─ Canais           (/canais)
  ├─ Produtos         (/produtos)
  ├─ Qualidade        (/qualidade)
  ├─ Financeiro       (/financeiro)
  ├─ Regiões          (/regioes)
  └─ Tempo Real       (/tempo-real)      — sem filtros globais (por natureza)

Pedidos
  └─ Geral            (/pedidos)
     (TikTok Shop / Mercado Livre — badge "Em breve", já existentes como disabled)

Inteligência
  └─ Ações ML + TikTok (/inteligencia)   — decidir se passa a herdar filtros globais

Operações
  └─ Criadores + Alertas (/operacoes)    — decidir se passa a herdar filtros globais
```

- **Filtros globais:** mantidos como hoje (canal, marca, período, comparação) via `useGlobalFilters`, agora exibidos no header ao lado da sidebar (não mais dentro do `<main>` de cada página) para reduzir duplicação visual.
- **Navegação contextual:** o padrão já existente de preservar querystring ao clicar de uma tabela para `/brand/[brand]` deve virar o padrão universal de todo drill-down de página (não só para marca).
- Não se propõe replicar o conceito de "torres" (Performance/RCA/CRM) do torre_b2b — a Torre de Marketplaces não tem perfil de vendedor/representante.

## 7. Especificação funcional da nova Gerencial

Seções, em ordem, reaproveitando o que já existe e fechando os gaps identificados:

1. **Header + filtros globais** (já existe, mover para fora do fluxo de scroll do conteúdo).
2. **Resumo executivo** (`ExecutiveSummaryCard`, já existe) — expandir para ser o ponto de entrada do fluxo resumo→desvio: cada item de "O que mudou"/"Atenções" já linka para detalhe (`item.href`); garantir que todo link aponte para um drill-down real (hoje pode apontar para páginas que ainda não fecham o ciclo).
3. **KPIs principais** (GMV, Pedidos, Ticket Médio, ROAS — já existem) — avaliar se cada card deveria ser clicável para abrir explicação (hoje não são).
4. **Tabela de performance por marca** (`BrandPerformanceTable`, já existe, com metas) — manter; adicionar paginação/busca só se o número de marcas crescer (hoje são 5, não é urgente).
5. **Tendência do período** (`TrendChart`, já existe) — avaliar adicionar modo alternativo (ex: GMV vs. Pedidos) inspirado no toggle Faturado/Pedido Colocado do torre_b2b, mas só se houver necessidade real identificada no U1.
6. **Alertas operacionais** (hoje 1 regra hardcoded para Lescent ML) — generalizar como lista de alertas dirigida por dado, não por regra fixa no componente.
7. **Comportamento:** loading via skeleton (já existe); erro via banner com retry (já existe); vazio via mensagem central (já existe); dado desatualizado via badge live/demo + timestamp (já existe) — nenhum destes precisa ser reconstruído, só reorganizado sob a nova navegação.

## 8. Mapa de interações e drill-downs

| Elemento clicado | Destino | Contexto preservado | Informação apresentada | Dado/API existente | Gap real |
|---|---|---|---|---|---|
| Nome da marca na `BrandPerformanceTable` | `/brand/[brand]` (já existe) | Canal/marca/período via querystring | KPIs da marca, diário, mix de canal | Sim (`fetchBrandDetail`) | Nenhum |
| Item de "O que mudou" / "Atenções" no `ExecutiveSummaryCard` | `item.href` (já existe) | Depende do href gerado pelo backend | Depende do destino | Depende — não auditado no backend (fora do escopo desta auditoria de frontend) | Confirmar no backend que todo `href` gerado aponta para uma rota real e navegável |
| Card de KPI (GMV, Pedidos etc.) | Nenhum hoje | — | — | Dado agregado já existe | Dado existe, mas o frontend não expõe explicação por clique — oportunidade real de fechar "resumo→desvio→clique" |
| Linha de tabela em Canais/Financeiro/Qualidade | Nenhum drill-down hoje | — | — | Dado é só agregado por marca×canal | Dado existe no nível exibido; detalhe abaixo desse nível (ex: por dia, por SKU) não foi localizado nos tipos atuais |
| Etapa do funil de pedidos (conceito do torre_b2b) | Não existe na Torre | — | Lista de pedidos individuais na etapa | **Dado não localizado** — `PedidosData`/`PedidosBrandRow` são agregados, não há tipo de pedido individual em `api-client.ts` | Gap real se este drill-down for priorizado; caso contrário, fora do escopo |
| UF no mapa de Regiões | Filtro local (`ufFilter`), já existe | Não é URL, é state local por design documentado | Recalcula tabelas da própria página | Sim | Nenhum — já funcional, decidir apenas se deve virar modal no revamp ou continuar filtro inline (ambos válidos) |
| Produto na lista de Produtos/Inteligência | Nenhum drill-down hoje | — | — | Dado de produto já existe (`ProdutoMLRow` etc.) | Dado existe; não há tela de detalhe de produto — avaliar necessidade real antes de propor |

## 9. Direção visual

O `DESIGN.md` já aprovado (não alterado nesta auditoria) já define um sistema completo — a direção visual do revamp deve **herdar**, não recriar:

- **Tipografia:** stack de sistema, hierarquia Display/Headline/Title/Body/Label já definida — manter.
- **Cores:** violeta primário (#7c3aed) com "regra de disciplina" (máx. 4 usos por tela), cores de marca reservadas a avatares, semáforo verde/âmbar/rosa só para status — manter.
- **Densidade/espaçamento:** cards brancos sobre fundo lavanda, bordas violeta-sutis, sem cards aninhados — manter.
- **Cards/tabelas/gráficos:** convenções já documentadas (`rounded-2xl`, `tabular-nums`, hover com shadow estrutural) — manter.
- **Modais/drawers:** **não existe ainda** nenhuma convenção no `DESIGN.md` para modal ou drawer — precisa ser especificada no U1 antes de qualquer drill-down analítico ser implementado, usando o padrão do torre_b2b (modal com `createPortal`, filtros internos, rodapé de ação) como inspiração de estrutura, não de estilo.
- **Responsividade/acessibilidade:** já bem coberta (ver §7 do relatório de auditoria do app atual) — manter os padrões de `aria-live`, `aria-sort`, `focus-visible`.

**Atualização documental necessária no U1 (não é mais bloqueador):** o `DESIGN.md` diz, na seção "Don't": *"Don't use a charcoal or dark sidebar as primary navigation... Navigation lives in the page header."* Essa regra foi escrita antes da decisão de produto que aprovou a sidebar persistente. Não há incompatibilidade real: a decisão já aprovada é explícita — **sidebar clara/lavanda, integrada à identidade GoBeauté, nunca charcoal/dark** — ou seja, a regra "Don't" continua válida na sua intenção (não usar sidebar escura), só precisa de uma redação que reconheça a existência da sidebar como navegação primária. Isso é uma tarefa estreita e delimitada do próprio Gate U1 (atualizar apenas as regras necessárias do `DESIGN.md`), não um pré-requisito que trava o início do U1.

## 10. Componentes que podem ser reutilizados

Praticamente toda a camada de dados e boa parte dos componentes visuais já existentes devem ser preservados:

- `useGlobalFilters` (hook de filtros — mais robusto que o padrão da referência).
- `api-client.ts` e todos os tipos/funções de fetch.
- `KpiCard`, `Skeleton` (`SkeletonKpiCard`/`SkeletonTableRows`), `SortableHeader`, `useSortableTable`, `TableScrollHint`.
- `ExecutiveSummaryCard` (base do novo fluxo resumo→desvio→explicação).
- `BrandPerformanceTable`, `GoalAttainment` (padrão de metas já equivalente ao da referência).
- `RegioesBrazilMap` (já equivalente ao mapa de heatmap do torre_b2b).
- `TrendChart`, `DailyChart`, `ChannelMixChart`, `HourlyChart` (Recharts já em uso, mesma lib da referência).
- `DateRangeFilter`, `MarketplaceFilter`, `BrandFilter` (podem alimentar um futuro `MultiFilter` genérico, sem descartar a lógica de negócio já validada).

## 11. Componentes que precisam ser criados

- **Sidebar** persistente (desktop) + **drawer** (mobile), substituindo `AppNav` — respeitando a resolução do conflito do §9.
- **`MultiFilter` genérico** (inspirado no padrão do torre_b2b), para consolidar `MarketplaceFilter`/`BrandFilter` num único componente configurável.
- **Modal de drill-down analítico** — padrão ainda inexistente na Torre, necessário para qualquer clique-para-explicação além da navegação de página já existente.
- **`loading.tsx` por rota** (Suspense do App Router) — hoje só há loading client-side.
- **Wrapper de lazy loading** (`next/dynamic`) para gráficos/mapa pesados.
- **Componente de paginação + exportação XLSX** genérico para tabelas (hoje só Produtos pagina; exportação não existe em lugar nenhum).
- **Error boundary** de página — hoje uma falha de fetch não propagada corretamente poderia derrubar a página inteira (mitigado parcialmente pelos `try/catch` no `api-client.ts`, mas sem boundary de render).

## 12. Mudanças de backend eventualmente necessárias (classificadas, sem implementar)

| Mudança | Classificação | Motivo |
|---|---|---|
| Endpoint de pedidos individuais (não só agregados) | **Fora do escopo — não será criado sem nova decisão explícita** (decisão de produto já registrada) | Hoje só existem agregados por marca/canal/dia |
| `href` de todo `ExecutiveInsight` apontando para uma rota navegável real | Necessário — validar antes de tornar esses itens clicáveis no U2 (não é pré-requisito do U1) | Não auditado neste gate (auditoria foi só de frontend) |
| Exposição de margem/CMV real nos 3 marketplaces | Dívida já registrada em memória de projeto anterior (`Gate 1 preço médio/margem`) — segue bloqueada, não é escopo deste revamp | Sem CMV consolidado; só ML tem ROAS/ACOS real por produto |
| Endpoint de detalhe de produto individual | Fora do escopo — avaliar demanda real antes de propor | Hoje só há listas paginadas de produto, sem tela de detalhe |

## 13. Sequência dos Gates U1–U6

Paginação, exportação, lazy loading, error boundaries e testes **não são gates separados** — são tasks ou critérios de aceite dentro do gate em que se tornarem necessários.

### U1 — Fundação visual e shell

- Atualizar apenas as regras necessárias do `DESIGN.md` para registrar a sidebar clara/lavanda (decisão de produto já aprovada) — atualização estreita, feita dentro do próprio U1, não como pré-requisito externo.
- Implementar sidebar desktop e drawer mobile, substituindo `AppNav`.
- Implementar header e área consistente para os filtros globais (`useGlobalFilters` mantido como está).
- Definir convenção visual e de acessibilidade para modal/drawer (ainda inexistente no `DESIGN.md`).
- Preservar todas as rotas e regras de dados existentes — nenhuma métrica, rota ou endpoint novo.
- Adicionar rede de segurança mínima de typecheck/build (ex: `tsc --noEmit` antes do build), sem introduzir lint ou dependência nova sem necessidade demonstrada.

### U2 — Gerencial completa e padrão de drill-down

- Reorganizar resumo executivo, KPIs, desempenho por canal/marca, tendência e alertas sob a nova navegação.
- Tornar clicáveis **somente** elementos com destino e dados verificáveis — nada de clique decorativo.
- Implementar o primeiro padrão reutilizável de drill-down analítico, somente leitura.
- Validar os `href` do `ExecutiveSummaryCard` **antes** de usá-los como destino de clique.
- Preservar filtros globais (canal/marca/período/comparação) no clique, no fechamento do drill-down e na navegação de volta.
- Tratar loading, vazio, erro, dado parcial e stale data em todos os elementos tocados.

### U3 — Canais e Marcas

- Revamp das telas de Canais e das páginas de Marca (`/brand/[brand]`).
- Comparações canal × marca × período usando os contratos de dado já existentes.
- Aplicar o padrão de drill-down estabilizado no U2.
- Manter os contratos de API existentes; backend novo somente se for bloqueador real (não antecipar).

### U4 — Produtos, Regiões e Financeiro

- Revamp das três áreas.
- Padronizar rankings, tabelas, ordenação, paginação e exportação **onde fizer sentido** (não é obrigatório em toda tabela).
- Aplicar lazy loading aos componentes pesados efetivamente tocados neste gate (não a todo o app de uma vez).
- Não criar detalhe abaixo do grão de dado hoje disponível.

### U5 — Qualidade, Tempo Real, Pedidos, Inteligência e Operações

- Revamp das cinco telas restantes.
- Manter Tempo Real independente de filtros históricos (por natureza do dado).
- Avaliar separadamente, para Inteligência e Operações, se cada uma deve herdar marca/canal/período — não presumir que todos os filtros globais se aplicam a todas as telas.
- Não criar pedido individual, ações de escrita ou workflow operacional novo.
- Não remover ou fundir rotas sem aprovação explícita.

### U6 — QA integrado e fechamento

- Responsividade desktop/tablet/mobile nas telas tocadas pelo revamp.
- Navegação por teclado, foco visível e contraste.
- Typecheck, build e os testes de lógica já existentes (`node --test`).
- Smoke test de todas as rotas (a página carrega sem erro).
- Pelo menos um fluxo de integração cobrindo filtro → navegação → drill-down, usando a infraestrutura de teste já disponível ou o menor acréscimo justificável (não introduzir um framework de E2E novo só por completude).
- Revisão de desempenho e lazy loading do que foi implementado nos gates anteriores.
- Documentação final e atualização do `PROJECT_STATUS.md`.

## 14. Critérios de aceite e definição de pronto (deste Gate U0)

- [x] `docs/UI_REVAMP_PLAN.md` criado com as 15 seções solicitadas.
- [x] Nenhum arquivo de código alterado (`apps/web/**` intocado).
- [x] `DESIGN.md` e `docs/shopee_datamart_daily_jobs_handoff.md` preservados exatamente como estavam.
- [x] Nenhum arquivo do torre_b2b copiado para o workspace ou para o Git.
- [x] `docs/PROJECT_STATUS.md` atualizado apenas nos pontos autorizados (frente ativa, Shopee API pausada, ponteiro para este documento).
- [x] Repositório de referência acessado somente para leitura, em diretório temporário fora do workspace, removido ao final.
- [x] Rodada única de correção aplicada: roadmap U1–U6 alinhado às decisões de produto já aprovadas, conflito de sidebar reclassificado (não é mais bloqueador), achados reclassificados.
- [x] **Gate U0 encerrado.** Não há U0.1; próxima etapa é o Gate U1, mediante nova aprovação explícita para iniciar implementação.

## 15. Riscos e achados classificados (reclassificado nesta rodada de correção)

**Bloqueadores:**
- Nenhum no encerramento do U0. O conflito de sidebar com o `DESIGN.md` deixou de ser bloqueador — é uma decisão de produto já aprovada, com execução prevista dentro do próprio U1 (ver §9 e §13).

**Necessários (tasks/critérios de aceite dos gates onde se aplicam, não gates próprios):**
1. Atualização estreita do `DESIGN.md` no U1, para registrar a sidebar clara/lavanda como navegação primária aprovada.
2. Convenção visual e de acessibilidade de sidebar/drawer/modal, especificada no U1.
3. Validação dos `href` do `ExecutiveSummaryCard` antes da implementação correspondente no U2 (não antes do U1).
4. Rede mínima de typecheck/build (`tsc --noEmit` + `next build`) para proteger as mudanças de frontend a partir do U1 — sem introduzir lint ou dependência nova sem necessidade demonstrada.
5. Tratamento consistente dos estados loading/vazio/erro/parcial/stale em todas as telas efetivamente tocadas em cada gate (U2 em diante).

**Dívidas (registradas, não bloqueiam, não urgentes):**
6. Ausência atual de testes de componente/E2E — a ser reduzida proporcionalmente no U6, sem framework novo além do necessário para 1 fluxo de integração.
7. `api-client.ts` centralizado e grande (59 KB, ~60 interfaces) — não refatorar automaticamente; só se um gate específico exigir.
8. Filtros de marca/canal são componentes ad-hoc (`MarketplaceFilter`/`BrandFilter`) — só extrair um `MultiFilter` genérico se o U1/U2 demonstrar repetição concreta, não por antecipação.
9. Ausência de lint configurado (`.eslintrc`/`eslint.config` inexistentes) — não adicionar dependência/configuração apenas por convenção; typecheck/build já cobre a rede mínima de segurança.

**Fora do escopo (a referência B2B não se aplica à Torre, ou está fora do projeto por decisão de produto):**
10. Endpoint de pedidos individuais — decisão de produto: não será criado neste projeto sem nova decisão explícita.
11. Matriz de Compras (SKU × mês × cliente) — conceito de negócio B2B corporativo, sem equivalente real na Torre.
12. Comissão de vendedor, inadimplência por título/boleto, cadastro de vendedores e multi-tenant de "torres" (Performance/RCA/CRM) — sem análogo em um marketplace B2C com pagamento à vista e sem representante.
13. Alterações de pipeline, banco, API Shopee ou arquitetura Neon/Data Mart — este revamp é estritamente de frontend/UX.
14. Qualquer ação operacional que escreva dados (o revamp cobre apenas drill-downs analíticos e somente leitura).
15. Sistema de permissões por aba (`adminOnly`/`strictAba`/`hardGate`) — não há auth/roles hoje na Torre; não inventar esse requisito sem evidência de necessidade real.

## Limitações desta auditoria

- A referência torre_b2b foi auditada exclusivamente por evidência de código e documentação (`CLAUDE.md`, `TORRE_B2B_CONTEXTO_COMPLETO.md`) — não foi executada localmente (exigiria instalar dependências e credenciais Supabase, fora do escopo autorizado deste gate).
- A disponibilidade de dado para cada drill-down foi avaliada apenas pelos tipos/contratos já expostos em `apps/web/src/lib/api-client.ts` — nenhuma consulta direta a banco foi feita, conforme instrução do gate.
- O `href` de destino dos itens do `ExecutiveSummaryCard` não foi validado contra o backend (auditoria foi de frontend); tratar como suposição a confirmar no U1/U2.
