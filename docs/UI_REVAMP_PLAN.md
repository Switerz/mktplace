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

**Status do Gate U4 — Produtos, Regiões e Financeiro:** **IMPLEMENTADO, AGUARDANDO REVISÃO** (24/07/2026), após uma rodada de implementação **e uma rodada consolidada de correção pré-commit** (também 24/07/2026) que resolveu 4 findings de revisão. Nenhuma métrica, rota, endpoint ou regra de negócio foi alterada em nenhuma das duas rodadas; `apps/api`/pipelines/banco intocados. U4 permanece "aguardando revisão" até aprovação explícita — não foi feito commit/push/deploy, e o U5 não foi iniciado.

- **Produtos** (`apps/web/app/produtos/page.tsx`): reorganizada na ordem cabeçalho → badge de fonte da aba ativa → abas → filtros da aba → linha compacta "Escopo atual" → resumo Pareto → botão de exportação → tabela paginada. Exportação "Exportar página (.csv)" (`ProductExportButton.tsx` + `src/lib/produtos-export.ts`).
- **Regiões** (`apps/web/app/regioes/page.tsx`): reorganizada na ordem cabeçalho (novo) → filtros globais + UF local → avisos de cobertura → KPIs → mapa (lazy) → Ranking por UF → Cobertura por Marca × Canal → Tendência.
- **Financeiro** (`apps/web/app/financeiro/page.tsx`): reorganizada na ordem cabeçalho (novo) → badge live/mock protegido → filtros globais → KPIs → navegação interna → tabelas por canal com `TableScrollHint` e link de marca → `/brand/[brand]`.

**Rodada consolidada de correção pré-commit (24/07/2026) — 4 findings, únicos, sem novo escopo:**

1. **FINDING 1 (bloqueador) — identidade de requisição em Produtos.** A implementação original de Produtos dependia só de `startFetch()`/`resolveFetch()` (`async-channel-state.ts`) para invalidar dados obsoletos — cobria respostas de rede fora de ordem, mas não o frame de render *anterior* ao `useEffect` rodar (troca de aba/marca/período/bucket/página/ordenação podia mostrar a tabela, o resumo Pareto, o badge live/mock e o "Escopo atual" da identidade ANTERIOR por um frame, incluindo ao retornar a uma aba já visitada). Corrigido com `src/lib/produtos-request-key.ts`: chaves de identidade *separadas* para tabela e resumo, por canal (`buildMlTableKey`/`buildMlSummaryKey` para ML; `buildPeriodTableKey`/`buildPeriodSummaryKey`, compartilhadas, para TikTok/Shopee — mesmo contrato de parâmetros), cada uma com sua própria `resolvedKey` guardada junto da resolução (`mlResolvedKey`, `tkResolvedKey`, etc.). `resolveChannelAvailability` computa `"loading" | "unavailable" | "available"` por tabela/resumo — `"loading"` cobre tanto a busca em andamento quanto a chave desatualizada (o frame pré-efeito). Seis novas constantes `xxDisplayData`/`xxDisplaySummary` (só `"available"`) substituem toda leitura direta de `mlState.data`/`mlSummaryState.data` etc. na renderização — tabela, resumo, badge, "Escopo atual" e linhas de exportação nunca mais mostram uma identidade antiga. O mecanismo de descarte de resposta fora de ordem (`mlReqId`/`resolveFetch`) foi preservado sem alteração.
2. **FINDING 2 (necessário) — loading/error/fresh separados em Regiões e Financeiro.** As duas páginas usavam `!dataIsFresh` como sinônimo de "carregando"; depois de um erro definitivo, `dataIsFresh` ficava `false` para sempre, deixando skeleton/opacidade/`aria-busy=true` ligados como se ainda estivesse buscando. `src/lib/request-freshness.ts` ganhou `computeRequestStatus`, que separa os 3 estados sempre mutuamente exclusivos: `loading` (busca em andamento OU chave atual ainda não resolvida — inclui o clique em "Tentar novamente", que já muda `retryKey`/`requestKey` antes do próprio efeito rodar), `error` (erro definitivo com a chave atual já resolvida) e `fresh`. Aplicado nas duas páginas: erro definitivo agora renderiza *só* cabeçalho, filtros e o banner de erro (um bloco dedicado substitui KPIs/mapa/tabelas — nunca skeleton, nunca dado antigo, `aria-busy=false`, o mapa nunca recebe `loading=true` porque nem é montado).
3. **FINDING 3 (necessário) — parcial ≠ vazio ≠ indisponível.** Regiões: `summary == null` continua sendo o único gatilho de erro total; mas se `summary` funcionar e `byUf`/`byBrand`/`trend` vier `null` individualmente (endpoint específico falhou), o resultado passou a ser tratado como **fresh porém parcial** — as seções resolvidas continuam exibidas normalmente, um único aviso âmbar compacto (`describeRegioesPartialSections`/`formatRegioesPartialWarning`, `regioes-request-key.ts`) identifica quais seções ficaram indisponíveis, e a mensagem "Sem dados..." de cada tabela vazia agora diz "Dados indisponíveis..." quando a causa é `null` (nunca confundido com um array `[]` de sucesso real). Produtos: tabela e resumo Pareto continuam podendo carregar de forma independente (Finding 1); quando exatamente um dos dois está `"available"` e o outro `"unavailable"`, `describeProdutosPartialWarning` (`produtos-request-key.ts`) mostra o mesmo tipo de aviso compacto; quando os dois falham, os estados de erro/offline já existentes de cada componente bastam (nenhum aviso duplicado); a exportação só fica habilitada quando a tabela da identidade atual está `"available"`.
4. **FINDING 4 (bloqueador) — remoção do `xlsx` da produção.** `xlsx@0.18.5` (promovida a `dependency` na implementação original do U4) tem vulnerabilidades de alta severidade sem correção disponível na linha 0.18.x. Confirmado via `rg` que o único uso funcional no repositório era o `import("xlsx")` introduzido neste mesmo gate (as demais ~60 ocorrências são a extensão de arquivo `.xlsx` em pipelines/docs do Shopee, sem relação com o pacote npm). Removida completamente de `package.json`/`package-lock.json` (via `npm install --package-lock-only` + `npm prune`, sem substituto instalado) — 9 pacotes a menos (`xlsx` + `adler-32`/`cfb`/`codepage`/`crc-32`/`ssf`/`wmf`/`word`). Botão renomeado para "Exportar página (.csv)"; `src/lib/produtos-export.ts` ganhou `buildProdutosCsv`/`buildProdutosCsvFile` (geração 100% síncrona, sem biblioteca): separador `;`, escape de `;`/aspas/quebra de linha, `null` sempre vira célula vazia (nunca `0`), proteção contra CSV/formula injection (strings iniciadas por `=`/`+`/`-`/`@` recebem prefixo `'`, aplicado só a texto — nunca a números negativos legítimos), BOM UTF-8 (`﻿`) prefixado ao conteúdo final. `ProductExportButton.tsx` monta o arquivo com `Blob`+`URL.createObjectURL`+link sintético, sempre revogando a object URL (`finally`); erro de exportação exposto com `role="alert"`/`aria-live="assertive"`, sem alterar `loading`/dados da página. Nome de arquivo mantém o mesmo contrato determinístico/sanitizado, só com a extensão trocada para `.csv`.

**Patch final estreito (25/07/2026) — 1 finding adicional, wiring de erro:** revisão final encontrou que o Finding 2 acima ficou incompleto: nos caminhos de FALHA de Regiões (`sm == null` e `.catch()`) e Financeiro (`.catch()`), o código atualizava `error`/`loading` mas nunca chamava `setResolvedKey(key)`. Como `computeRequestStatus` só classifica como `"error"` quando a chave atual já foi *resolvida* (`resolvedKey === requestKey`), uma falha real ficava presa em `"loading"` para sempre (o banner de erro aparecia — ele depende só do `error` state local — mas o restante da página nunca saía do estado de carregamento). Corrigido adicionando `setResolvedKey(key)` nos 3 pontos de falha (2 em Regiões, 1 em Financeiro), preservando a guarda `if (ignore) return` em todos — uma resposta obsoleta continua nunca alterando `resolvedKey`/`error`/`loading` da identidade atual. Nenhuma mudança de tratamento parcial (`byUf`/`byBrand`/`trend`) foi feita. Adicionada regressão estática dedicada (`regioes-financeiro-resolvedkey-wiring.test.ts`, sem harness de componente React) que lê o código-fonte das duas páginas e confere que cada bloco de falha (`setError(...)` real, nunca o `setError(null)` de reset) contém `setResolvedKey(key)` e a guarda `if (ignore) return`.

- **Lógica compartilhada extraída e testada:** `src/lib/request-freshness.ts` (`isRequestFresh` + `computeRequestStatus`), `src/lib/produtos-request-key.ts`, `src/lib/regioes-request-key.ts` (identidade + escopo UF + seções parciais), `src/lib/financeiro-request-key.ts`, `src/lib/produtos-scope.ts`. Nenhuma dependência nova instalada nesta rodada — `xlsx` foi **removida**, não substituída.
- **Testes novos/ampliados na rodada de correção:** `produtos-request-key.test.ts` (chaves de tabela/resumo por canal, `resolveChannelAvailability` cobrindo troca de aba/filtro antes do efeito e retorno a aba visitada, `describeProdutosPartialWarning` nos 4 estados: sucesso vazio, indisponibilidade/null, parcial, erro total); `request-freshness.test.ts` ampliado com `computeRequestStatus` (loading/error/fresh mutuamente exclusivos, retry via `retryKey`); `regioes-request-key.test.ts` ampliado com `describeRegioesPartialSections`/`formatRegioesPartialWarning` (sucesso vazio vs. indisponível vs. parcial vs. erro total, distinguindo sempre `null` de `[]`); `produtos-export.test.ts` reescrito para o contrato CSV (colunas exatas por canal, só as linhas recebidas, ordem preservada, null vazio, escape de `;`/aspas/quebra de linha, proteção contra formula injection, números negativos não escapados, BOM UTF-8, filename `.csv`, ausência de import/uso funcional de `xlsx`); `regioes-financeiro-resolvedkey-wiring.test.ts` (novo, patch final — cobre o wiring de `setResolvedKey(key)` e a guarda `if (ignore) return` nos caminhos de falha das duas páginas). 315 testes, 0 falhas. `npm run typecheck` e `npm run build` passam.
- **Auditoria de dependências:** `npm audit --omit=dev --json` antes da correção: 4 vulnerabilidades altas (incluindo `xlsx`). Depois: **3 altas** — `next` (dependência **direta**, `isDirect: true` no relatório do `npm audit`) e `postcss`/`sharp` (dependências **transitivas** puxadas pelo próprio `next`, `isDirect: false`, listadas no `via` de `next`). As três são pré-existentes ao U4 (nenhuma foi introduzida por este gate) e ficam como dívida separada, não corrigidas aqui (fora do escopo autorizado desta rodada de patch).
- **Dívidas classificadas (não bloqueiam o encerramento):** validação visual em navegador real não foi possível nesta rodada nem na anterior (mesma limitação estrutural do U1–U3 — nenhum Playwright/MCP de navegador conectado nesta sessão); cobertura de componente React segue zero (mitigada por typecheck+build+testes de lógica pura extensivos); o fallback mock de `fetchCanais`/`fetchFinanceiro` (pré-existente) segue ignorando o filtro de marca no modo demonstração; as 3 vulnerabilidades altas remanescentes (`next` direta; `postcss`/`sharp` transitivas via `next`) ficam como dívida de dependências para tratamento futuro fora deste gate. **Fora do escopo:** nenhum endpoint/rota/tabela/métrica novo; nenhum modal novo; nenhuma alteração em `apps/api`/pipeline/banco/Neon; nenhuma ferramenta de navegador instalada; U5 não iniciado. **Próximo gate: U5 — Qualidade, Tempo Real, Pedidos, Inteligência e Operações**, mediante aprovação explícita do U4.

**Status do Gate U5 — Qualidade, Tempo Real, Pedidos, Inteligência e Operações:** **CONCLUÍDO/APROVADO** (28/07/2026), em uma rodada de implementação (26/07) e uma rodada consolidada de correção (28/07) que resolveu os 2 findings abaixo, versionadas a partir de `cd3863f`, o commit do U4. Nenhuma métrica, rota, endpoint, workflow de escrita ou regra de negócio foi alterada; `apps/api`/pipelines/banco intocados.

**Decisão de filtros por tela (autoritativa, replicada do prompt do gate):**
- **Qualidade** (`apps/web/app/qualidade/page.tsx`): continua usando `useGlobalFilters` (canal/marca/período/compare), fallback mock e `mockLimitationNote` preservados.
- **Pedidos** (`apps/web/app/pedidos/page.tsx`): continua usando `useGlobalFilters` (canal/marca/período, sem compare — endpoint não entrega). Fonte cobre só TikTok Shop/ML; Shopee isolada mostra estado de indisponibilidade dedicado (sem KPIs/cards/tabela enganosos, sem sequer disparar fetch); Shopee combinada com TikTok/ML mostra aviso de que os números refletem só os canais suportados.
- **Tempo Real** (`apps/web/app/tempo-real/page.tsx`): não usa filtros globais — exclusivo de TikTok Shop e do dia corrente, marca/modo do gráfico continuam controles locais.
- **Inteligência** (`apps/web/app/inteligencia/page.tsx`): não herda filtros globais — `fetchInteligencia()` não recebe canal/marca/período; nota explícita de escopo adicionada; filtro local de marca preservado (afeta só as 3 seções de produto ML: Urgente/Escalar/Testar Ads).
- **Operações** (`apps/web/app/operacoes/page.tsx`): não herda filtros globais — `fetchOperacoes()` não recebe canal/marca/período; nota explícita de escopo adicionada; filtro local de marca preservado (afeta só a tabela de Top Criadores).

**Padrão de identidade de requisição (Gate U4) replicado nas 5 telas:** `resolvedKey`/`requestKey`/`computeRequestStatus` (`src/lib/request-freshness.ts`, inalterado) — `dataIsFresh`/`isLoadingState`/`isErrorState` sempre mutuamente exclusivos, leituras da UI sempre via constantes `display*` protegidas, falha de requisição sempre conclui `setResolvedKey(key)` antes de `setLoading(false)` (senão a falha fica presa em "loading" para sempre — mesmo Finding 2 do U4). Novas libs puras e testáveis: `src/lib/quality-request-key.ts`, `src/lib/pedidos-request-key.ts` (inclui `computePedidosCoverage`, decisão pura de cobertura Shopee), `src/lib/tempo-real-status.ts` (`computeTempoRealStatus`, os 5 estados do polling). Inteligência/Operações não têm filtro algum — a identidade de requisição é só o próprio `retryKey`, mas ganharam a mesma guarda `let ignore = false` que faltava antes (uma resposta obsoleta de um retry anterior podia sobrescrever o retry atual — bug de wiring pré-existente, corrigido neste gate).

**Comportamento especial do polling de Tempo Real (Task 4, única exceção da matriz de estados):** reescrito com uma máquina de 5 estados sempre mutuamente exclusivos (`initial`/`updating`/`unavailable`/`stale`/`fresh`) computados por `computeTempoRealStatus`. Guardas adicionadas: `inFlightRef` (nunca duas requisições sobrepostas entre timer automático e botão manual), `mountedRef` (nunca `setState` após unmount), falha de fetch nunca chama `setData` nem `setLastUpdated` (o último dado válido é sempre preservado, explicitamente marcado como "Falha ao atualizar" em badge âmbar + banner), countdown reseta em toda tentativa concluída (sucesso ou falha — é só o agendamento da próxima tentativa, nunca uma alegação de sucesso). Esta é a única tela onde falha preserva dado — as outras 4 nunca herdam essa exceção (Task 7).

**Cobertura Shopee em Pedidos:** extraída para uma função pura (`computePedidosCoverage`) — Shopee isolada nunca dispara fetch (evita interpretar indisponibilidade de cobertura como falha de rede) e mostra bloco dedicado de indisponibilidade; Shopee combinada com TikTok/ML mostra aviso permanente e os cards/KPIs/série do gráfico/colunas da tabela por marca só incluem os canais efetivamente selecionados (nunca um canal desmarcado).

**Navegação interna/drill-downs adicionados:** âncoras compactas (`scroll-mt-24`) em todas as 5 telas — Qualidade (Resumo/Qualidade por Marca/Fidelização ML/Qualidade Shopee, condicionais à seleção e à existência de dado), Pedidos (Resumo/Por Canal/Tendência/Por Marca), Inteligência (Status Portfólio/Urgente/Escalar/Testar Ads/Pareto/LTV/Top TikTok), Operações (Alertas/Top Criadores/Performance de Lives/Velocidade ML/Trend TikTok). Tempo Real não ganhou nav interna (tela de cockpit único, sem múltiplas seções longas). Nenhuma rota nova, nenhum link de marca novo em Pedidos (mantido texto simples — link para `/brand/[brand]` seria semanticamente ambíguo nesta fonte, que não cobre Shopee).

**Acessibilidade/responsividade:** `aria-live`/`aria-busy` mantidos e ampliados (Tempo Real ganhou `aria-live` que não existia); tabelas largas de Inteligência/Operações migradas do `overflow-x-auto` simples para `TableScrollHint` (sombras de scroll + dica mobile, mesmo componente das demais telas); nenhuma linha de tabela inteira clicável; alvos de toque e foco visível preservados dos componentes já validados no U1–U4.

**Testes novos na implementação original (44, todos lógica pura ou regressão estática sobre o código-fonte — sem harness de componente React, mesma limitação estrutural do U1–U4):** `quality-request-key.test.ts` (6), `pedidos-request-key.test.ts` (10, inclui `computePedidosCoverage` nos 4 cenários de seleção), `tempo-real-status.test.ts` (6, inclui verificação de mútua exclusividade para as 16 combinações booleanas), `tempo-real-wiring.test.ts` (6, regressão estática: guarda de concorrência, guarda de unmount, falha nunca chama `setData`/`setLastUpdated`, timers sempre limpos), `u5-resolvedkey-wiring.test.ts` (16, regressão estática: guarda `ignore` + `setResolvedKey` na falha das 4 páginas com filtro, branch Shopee-isolada de Pedidos, isolamento dos filtros locais de Inteligência/Operações). Total após a implementação original: 359 testes, 0 falhas.

**Validação visual:** não realizada — nenhuma ferramenta de navegador (Playwright/MCP) conectada nesta sessão, mesma limitação estrutural do U1–U4. Não foi instalado Playwright nem simulado QA visual, conforme instrução do gate.

**Achados classificados:**
- **Necessário (corrigido neste gate):** Inteligência/Operações não tinham guarda contra resposta obsoleta de retry (`let ignore`) — corrigido; Pedidos podia disparar fetch para seleção Shopee-isolada e depender do backend ignorar o canal — corrigido (fetch nunca é disparado nessa seleção); Inteligência/Operações usavam `overflow-x-auto` simples em vez de `TableScrollHint` — corrigido; Tempo Real podia sobrepor requests entre timer e botão manual, e podia perder o último dado válido numa falha silenciosa — corrigido com `inFlightRef`/preservação de dado.
- **Dívida (pré-existente, não é regressão introduzida aqui):** cobertura de componente React segue zero (mesma dívida do U1–U4); fallback mock de `fetchQuality` ignora marca/período (já avisado via `mockLimitationNote`, comportamento preservado por instrução explícita do gate); as 3 vulnerabilidades altas de dependências (`next` direta; `postcss`/`sharp` transitivas) seguem não corrigidas, por instrução explícita deste gate.
- **Fora do escopo:** nenhum endpoint/rota/tabela/métrica/threshold novo; nenhuma regra de negócio alterada; nenhum pedido individual; nenhuma ação de escrita/workflow operacional; U6 não iniciado.

**Rodada de correção consolidada do U5 (28/07/2026) — 2 findings, únicos, sem novo escopo (não é U5.1):**

1. **FINDING 1 (necessário/bloqueador do aceite) — duas fontes de tempo incompatíveis em Tempo Real.** A implementação original tinha DOIS `setInterval` independentes: um tick de 1s decrementando `countdown` (exibido no botão de refresh), e um segundo `setInterval(() => doFetch(true), REFRESH_INTERVAL_S * 1000)` criado uma única vez na montagem, com seu próprio relógio. `doFetch` resetava `countdown` para 300s no `finally` de QUALQUER tentativa concluída (manual ou automática, sucesso ou falha), mas esse reset nunca afetava o segundo `setInterval`, que continuava disparando no horário ORIGINAL da montagem — depois de um refresh manual, o texto podia mostrar "4:00" enquanto o próximo fetch automático real já estava a 2 minutos de distância. Corrigido removendo o segundo `setInterval` por completo: o tick de 1s passou a ser a ÚNICA fonte de verdade do agendamento — um novo `useEffect` dependente de `[countdown]` dispara `doFetch(true)` exatamente quando `countdown` chega a `0`. Como `doFetch` sempre reseta `countdown` para `REFRESH_INTERVAL_S` no seu `finally` (sucesso ou falha, manual ou automático, mesma função compartilhada pelos 3 chamadores — carga inicial, botão manual e este novo efeito), qualquer conclusão de tentativa reagenda corretamente o próximo ciclo real, nunca só o número exibido. `inFlightRef`/`mountedRef`/preservação de dado em falha/`lastUpdated` só no sucesso/status `stale`↔`unavailable` — todos preservados integralmente, sem nenhuma mudança de comportamento além da fonte de agendamento. Nenhuma biblioteca nova.
2. **FINDING 2 (necessário) — Pedidos com seleção exclusivamente Shopee comunicava "API offline"/"dados carregados".** O caminho `showShopeeOnly` já não disparava `fetchPedidos` (correto), mas marcava a identidade como resolvida sem erro/loading — `dataIsFresh` ficava `true`, fazendo o cabeçalho renderizar `<LiveStatusBadge live={false}>` ("Sem dados · API offline") e o `aria-live` anunciar "Dados de pedidos carregados.", confundindo "fonte sem cobertura Shopee" com falha de rede/sucesso. Corrigido checando `showShopeeOnly` ANTES do `LiveStatusBadge` no cabeçalho (badge neutro "Shopee sem cobertura nesta visão" nesse caminho) e adicionando um branch dedicado no `aria-live` ("Pedidos não possui cobertura Shopee nesta fonte."). O bloco de indisponibilidade já existente (corpo da página) e a ausência de chamada a `fetchPedidos` para Shopee isolada foram preservados sem alteração; a seleção mista (Shopee + TikTok/ML) continua com o `LiveStatusBadge`/aria-live de sucesso normais e o aviso de cobertura parcial já existente (`showShopeeMixed`), inalterados.

**Testes da rodada de correção (9 novos, 368 no total):** `tempo-real-wiring.test.ts` ganhou 4 testes novos e 1 ajustado (agora exatamente 1 `setInterval`/`clearInterval` real no arquivo — não mais 2; nenhum `setInterval(() => doFetch(...))` fora de comentário; o efeito `if (countdown === 0) doFetch(true)` depende de `[countdown]`; `doFetch` tem uma única definição compartilhada e reseta `countdown` dentro do `finally`). Novo arquivo `pedidos-shopee-only-semantics.test.ts` (5 testes): `showShopeeOnly` checado antes do `LiveStatusBadge`, branch de Shopee isolada nunca menciona "offline" e sempre menciona "sem cobertura", `aria-live` de Shopee isolada nunca diz "carregados" nem "offline", o branch de skip continua saindo antes de qualquer `fetchPedidos(`, e a seleção mista preserva o `LiveStatusBadge`/aria-live/aviso `showShopeeMixed` de sucesso normais. Todos os testes são lógica pura ou regressão estática sobre o código-fonte (sem depender de 5 minutos reais nem de harness de componente React) — 368 testes, 0 falhas. `npm run typecheck` e `npm run build` passam.

**Riscos restantes:** validação visual em navegador real segue pendente — fica para o Gate U6, sem bloquear o encerramento do U5; dependências vulneráveis (`next`/`postcss`/`sharp`) seguem sem correção. **Não foi aberto U5.1** (esta foi a única rodada de correção consolidada do U5). **Gate U5 aprovado em 28/07/2026** — 368 testes, `npm run typecheck` e `npm run build` passando. **Próximo gate: U6 — QA integrado e fechamento.** U6 não foi iniciado.

**Status do Gate U6 — QA integrado e fechamento:** **CONCLUÍDO** (28/07/2026), em três tasks: (Task 1) diagnóstico e QA estático — 368 testes, typecheck e build passando, mas **QA visual BLOCKED** por não haver navegador; (Task 2) **desbloqueio autorizado** com Playwright 1.61.0 + Chromium instalados de forma **temporária e isolada em `%TEMP%`** (`--no-save --no-package-lock`, `PLAYWRIGHT_BROWSERS_PATH` dentro da pasta temp, Chromium apenas), Torre servida na **porta 3100** (a 3000 é de outro projeto e não foi tocada), e **QA visual executado em desktop 1440×900, tablet 768×1024 e mobile 390×844**; (Task 3, esta) **rodada consolidada final** que corrigiu os 2 findings necessários do QA e retestou em navegador. Nenhuma métrica, rota, endpoint, regra de negócio, `apps/api`, pipeline ou banco foi alterado em nenhuma das tasks; nenhuma dependência entrou em `package.json`/`package-lock.json` (instalação do Playwright 100% em `%TEMP%`, removida ao final). Não foi aberto U6.1/U6.2.

**QA visual (Task 2) — resultado resumido:** todas as 12 rotas carregam (`/`, `/canais`, `/brand/barbours`, `/produtos`, `/regioes`, `/financeiro`, `/qualidade`, `/tempo-real`, `/pedidos`, `/inteligencia`, `/operacoes` + 404 real); navegação/shell, **drawer mobile** (abre/fecha por botão, backdrop e Escape; foco preso e devolvido ao acionador), **drill-down de KPI** (diálogo com foco preso, Escape, backdrop, retorno de foco, destino "Ver detalhamento por canal em Canais →"), **filtros→URL** (canal/marca/período refletidos na querystring, sem dado antigo apresentado como atual), **KPI→Canais** e **links de marca (Gerencial/Financeiro)** preservando querystring, **Pedidos Shopee-isolada** (sem fetch, badge neutro, sem "API offline", sem "dados carregados") e **Pedidos misto** (aviso de cobertura), e **estado de erro definitivo de Regiões** (banner dedicado + "Tentar novamente", sem skeleton infinito nem dado antigo). Como a API local estava offline, as telas rodaram no **fallback de demonstração/mock**; os `ERR_CONNECTION_REFUSED` de console são ambientais, não defeitos de frontend. **Veredito da Task 2: PASS WITH ISSUES** — 2 findings necessários (U6-01, U6-02) e 2 dívidas menores (U6-03, U6-04).

**Rodada consolidada final (Task 3, 28/07/2026) — 2 findings, únicos, sem novo escopo:**
1. **U6-01 (necessário) — "Performance por Marca" inacessível horizontalmente no mobile/tablet.** A tabela (~979px) vivia num card `overflow-hidden`, deixando ~340px visíveis e cortando GMV Total/metas sem qualquer scroll. Corrigido envolvendo **somente a tabela** no `TableScrollHint` já validado (mesmo padrão de Canais/Financeiro/Produtos), preservando card/cabeçalho/rodapé fixos, ordenação, links de marca e metas — nenhuma coluna escondida ou removida. Reteste: desktop sem hint quando tudo cabe; tablet/mobile com scroll interno, primeira e última coluna acessíveis, hint "arraste para ver mais", página inteira sem overflow horizontal, cabeçalho do card não rola junto, ordenação (aria-sort none→descending) e link de marca funcionando.
2. **U6-02 (necessário) — hydration error React #418 em Tempo Real.** `new Date()` rodava no render, divergindo entre SSR e primeiro render do cliente. Corrigido com um estado `clientReady` (false no SSR e no 1º render — ambos exibindo placeholder determinístico `—`/`--:--`) ativado em `useEffect`; só depois da montagem a data/hora locais são calculadas. Sem `suppressHydrationWarning`, sem nova fonte de agendamento (o tick de countdown, fonte única, segue intacto, assim como polling/`inFlightRef`/`mountedRef`/`lastUpdated`/estados stale). Reteste: **zero React #418**, zero pageerror, SSR renderiza o placeholder (`--:--`) e não uma hora real, data/hora aparecem no cliente após a montagem, indisponibilidade da API segue explícita.

**Testes/validação (Task 3):** `scroll-hint.test.ts` +3 (BrandPerformanceTable importa/usa `TableScrollHint`, `<table>` dentro dele, sem retorno ao anti-padrão "sem overflow-x"); `tempo-real-wiring.test.ts` +5 (`clientReady` = `useState(false)` ativado em `useEffect`, data/hora não vêm de `new Date()` sem guarda, placeholder determinístico, sem `suppressHydrationWarning` nem novo `setInterval`). **376 testes, 0 falhas; `npm run typecheck` e `npm run build` passando** (13 rotas geradas, mapa de Regiões lazy, sem `xlsx`). `npm audit --omit=dev` mantém as **3 altas conhecidas** (`next`/`postcss`/`sharp`), fora do escopo.

**Dívidas remanescentes (não bloqueiam o encerramento):** **U6-03** (overflow horizontal marginal de 3px em Pedidos no tablet — não piorou); **U6-04** (dois `<h1>` por página — topbar + título); **QA de fluxos com dado vivo bloqueado pela API local offline** (download CSV real de Produtos com o botão corretamente desabilitado offline; Canais detalhe marca×canal → Marca, cuja matriz é vazia por design em demonstração; e o refresh "stale" do Tempo Real após um sucesso real); **3 vulnerabilidades altas** de dependências (`next` direta; `postcss`/`sharp` transitivas); **ausência de teste automatizado de componente React** (mitigada por typecheck + build + testes de lógica pura/regressão estática). **Gate U6 concluído — Gates U0–U6 concluídos; revamp de UI/UX encerrado.** (Não implica deploy: nenhum commit/push/deploy foi feito.)

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
