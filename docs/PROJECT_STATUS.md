# Status geral — Torre de Controle de Marketplaces

**Última atualização:** 05/08/2026 (recuperação histórica ML/TikTok encerrada — junho, julho e 01–05/08 reconciliados; auditoria DQ1 concluída; Shopee atualizado até 04/08)
**Objetivo deste documento:** apresentar, em um único lugar, o estado das grandes frentes do projeto. Os detalhes técnicos, comandos e evidências continuam nos documentos específicos indicados em cada seção.

## Resumo executivo

A fase de **consistência e completude dos dados** foi concluída para janeiro a maio de 2026. TikTok, Shopee e Mercado Livre estão publicados no Neon e reconciliados com a referência XLSX; o erro agregado caiu de 7,05% para 1,3743%. Os resíduos restantes foram classificados como diferenças de fotografia, competência ou fonte manual, e não bloqueiam o encerramento.

Em paralelo, o primeiro ciclo completo do fluxo manual Shopee foi validado com dados de junho: Raw, Silver e Gold regional foram carregadas e reconciliadas para as cinco marcas. A transferência dessa rotina pode avançar enquanto a API oficial não estiver disponível. A sincronização regional de junho já foi concluída; uma rodada manual observada do `full_daily` também foi concluída com sucesso (`STATUS GERAL: OK`, sem executar Shopee). O Task Scheduler `mktplace_full_daily` foi habilitado em 23/07 e sua primeira execução agendada (24/07, 06:00) rodou automaticamente com sucesso — `STATUS GERAL: OK`, `ok_critical=true`, zero Shopee, sem intervenção manual. A automação diária de ML/TikTok/regional está, portanto, ativa e validada.

Uma nova frente foi aberta em 24/07: o **Revamp de UI/UX da Torre**. O Gate U0 (auditoria e especificação, sem implementação) foi concluído em 24/07, após uma rodada única de correção do roadmap — detalhes em [UI_REVAMP_PLAN.md](UI_REVAMP_PLAN.md). O Gate U1 (fundação visual e novo shell — sidebar clara/lavanda persistente no desktop, drawer mobile e topbar compartilhados) foi concluído e aprovado em 24/07, após uma rodada de correção de dois findings de revisão em runtime. O Gate U2 (Gerencial completa e padrão de drill-down) foi concluído em 24/07: a Gerencial foi reorganizada, os 4 KPIs principais ganharam drill-down agregado acessível (mesmo diálogo reutilizável) e um novo painel de desempenho por canal foi adicionado — tudo sobre os dados e contratos de API já existentes, sem alteração de backend/pipeline/banco. O Gate U3 (Canais e Marcas) foi concluído em 24/07, após uma rodada de implementação e uma rodada única de correção consolidada pré-commit: a página Canais foi reorganizada com navegação interna, resumos por canal e a matriz comparativa em destaque com um novo drill-down marca × canal (reaproveitando o mesmo diálogo do U2); a página de Marca ganhou link de volta preservando filtros e teve corrigido um fallback de modo demonstração que superestimava Pedidos/Ticket Médio sob seleção parcial de canal. Na rodada de correção, três findings foram resolvidos: Canais exibia dados antigos sob filtro/erro novo (corrigido com constantes `display*` protegidas por frescor), "Últimos 7 Dias" da página de marca reutilizava GMV combinado do mock sob seleção parcial de 2 canais (corrigido com uma projeção pura compartilhada entre gráfico e tabela), e o alvo de toque do botão "Detalhe" foi ampliado para 44×44px. O Gate U4 (Produtos, Regiões e Financeiro) foi implementado em 24/07 e passou por uma rodada consolidada de correção pré-commit (também 24/07), que resolveu 4 findings de revisão: (1) Produtos ganhou identidade de requisição própria por tabela e por resumo Pareto, em cada canal, corrigindo um frame de render anterior ao efeito em que a troca de aba/filtro podia mostrar dado/badge/escopo da identidade anterior; (2) Regiões e Financeiro separaram explicitamente os estados loading/error/fresh (antes um erro definitivo deixava skeleton/opacidade ligados como se ainda estivesse carregando); (3) Regiões passou a distinguir seção indisponível (`null`) de seção vazia com sucesso (`[]`), com um aviso compacto quando só uma parte dos dados falha, e Produtos ganhou o mesmo aviso quando exatamente tabela ou resumo falha; (4) a biblioteca `xlsx` (promovida a `dependency` na implementação original) foi **removida por completo** por ter vulnerabilidades de alta severidade sem correção disponível — a exportação de Produtos passou a gerar CSV (separador `;`, BOM UTF-8, proteção contra formula injection, sem nenhuma biblioteca). Um patch final estreito em 25/07 corrigiu um wiring incompleto do Finding 2: os caminhos de falha de Regiões e Financeiro atualizavam `error`/`loading` mas nunca concluíam `resolvedKey`, deixando a requisição atual presa em "loading" mesmo após um erro definitivo — corrigido com `setResolvedKey(key)` nos 3 pontos de falha, com regressão estática dedicada. As três páginas mantêm cabeçalho/hierarquia consistentes, `requestKey`/`resolvedKey` (com UF local incluída na identidade em Regiões), `RegioesBrazilMap` lazy via `next/dynamic`, e Financeiro com navegação marca→`/brand/[brand]` e `TableScrollHint` nas 3 tabelas. Filtros globais, contratos de API e regras de negócio preservados nos quatro gates; `npm audit --omit=dev` caiu de 4 para 3 vulnerabilidades altas — `next` é dependência **direta**, `postcss` e `sharp` são dependências **transitivas** relacionadas ao `next`; as três são pré-existentes e tratadas como dívida separada. O Gate U5 (Qualidade, Tempo Real, Pedidos, Inteligência e Operações) foi implementado em 26/07 numa única rodada: as 5 telas restantes ganharam o mesmo padrão de identidade de requisição do U4 (`resolvedKey`/`display*` protegidos por frescor), decisão de filtros por tela replicada do escopo aprovado (Qualidade/Pedidos herdam filtros globais; Tempo Real/Inteligência/Operações não herdam), cobertura Shopee isolada/combinada tratada explicitamente em Pedidos, e o polling de Tempo Real reescrito com uma máquina de 5 estados que nunca mais perde o último dado válido numa falha silenciosa nem sobrepõe requisições. 44 novos testes (359 no total), typecheck e build passando. Uma rodada de correção consolidada do U5 em 28/07 resolveu 2 findings de revisão: (1) Tempo Real tinha dois relógios independentes — o countdown exibido e um `setInterval` de auto-refresh próprio, criados separadamente — que podiam divergir após um refresh manual ou uma tentativa demorada (o texto mostrava um prazo que não correspondia ao próximo fetch real); corrigido unificando em uma única fonte de verdade: o countdown chegar a zero passou a ser o único gatilho do refresh automático; (2) Pedidos com seleção exclusivamente Shopee (sem cobertura nesta fonte) exibia badge "API offline" e anunciava "dados carregados" via `aria-live`, confundindo ausência de cobertura com falha de rede/sucesso; corrigido com um indicador neutro e uma mensagem de acessibilidade específica. 9 testes novos (368 no total), typecheck e build passando; nenhuma métrica/rota/dependência alterada; sem U5.1. **Gate U5 aprovado em 28/07/2026.** O **Gate U6 (QA integrado e fechamento) foi concluído em 28/07/2026**, encerrando o revamp: o QA visual pendente desde o U1 foi finalmente executado em navegador (Playwright + Chromium temporários e isolados em `%TEMP%`, Torre na porta 3100) em desktop/tablet/mobile — as 12 rotas, o drawer, os drill-downs, os filtros→URL e os estados de erro foram validados — e a rodada consolidada final corrigiu os 2 últimos findings necessários (scroll horizontal interno na tabela "Performance por Marca" e o hydration error React #418 em Tempo Real), com 376 testes, typecheck e build passando. **Gates U0–U6 concluídos.** Em 28/07 o commit `9fcf72a` foi publicado automaticamente na Vercel (integração GitHub→Vercel) e a **auditoria pós-deploy foi encerrada em 03/08/2026 como GO COM RESTRIÇÃO**: deployment Production **Ready** do commit `9fcf72a`, domínio canônico `https://mktplace-gobeaute.vercel.app` respondendo (200 nas 11 rotas), `https://mktplace-blond.vercel.app` como alias/redirecionamento, bundle apontando para o backend público (`mktplace-api.onrender.com`, sem `localhost`/IP local), API online (`openapi.json` 200) e CORS correto para o domínio canônico. O fallback "Demonstração · API offline" observado antes ocorria **apenas na URL efêmera de deployment** — atrás do SSO da Vercel e fora da allowlist de CORS do backend — e **não** representa falha do deployment nem erro de `NEXT_PUBLIC_API_URL`. Restrições em aberto: o domínio canônico está público sem autenticação própria da Torre (decisão de acesso pendente), o smoke visual completo em produção não foi automatizado, e o campo "About" do GitHub ainda aponta para a URL antiga (`mktplace-one.vercel.app`, que retorna `DEPLOYMENT_NOT_FOUND`). Em paralelo, a criação dos apps oficiais Shopee por marca foi pausada para priorizar essa frente.

## Portfólio de frentes

| Frente | Status | Estado atual | Próximo marco | Dependência ou atenção |
|---|---|---|---|---|
| Consistência XLSX × Torre | **CONCLUÍDO** | Os três canais foram publicados e reconciliados nas 70 células de jan–mai. O erro agregado caiu de 7,05% para 1,3743%. | Manter monitoramento e tratar novos desvios somente quando materialmente relevantes. | Resíduos históricos aceitos e documentados. |
| Completude Mercado Livre | **CONCLUÍDO** | Backfill de Barbours, Kokeshi e Lescent validado; 20 células completas; 603 linhas publicadas no Neon e reconciliadas diariamente com o Data Mart. | Acompanhar as cargas recorrentes. | Regra atual de GMV mantida; resíduos de snapshot não bloqueantes. |
| Shopee junho — Raw/Silver/Gold | **CONCLUÍDO** | Lote de junho carregado e reconciliado nas três camadas para as cinco marcas. A Gold recebeu 3.213 linhas e R$ 6.607.166,51 de GMV na janela de 01–30/06. | Manter a rotina manual documentada. | Data Mart e Neon sincronizados em 23/07. |
| Shopee agosto (parcial, mês em andamento) | **CARGA PARCIAL CONCLUÍDA (01–04/08)** | Primeira carga incremental de agosto executada em 05/08 com dados até **2026-08-04** (data máxima real dos exports): batch `62306b94…` (16 arquivos novos: 6 orders + 5 stats + 5 ads, 11.812 linhas), Raw committed/reconciliado, Silver +11.499 orders/+25 stats/+288 ads (pendência 0, reconcile 16/16), Gold regional committed (run_id `ago05112427`, **437 linhas, GMV R$ 703.910,22, 9.759 pedidos**, backup+`.sha256` íntegros, diagnose pós `would_change_data=false`), Daily publicado (20 chaves = 5 marcas × 4 dias, GMV shop-stats **R$ 677.164,23**, 3 `source_sync_run=success` janela exata 08-01..04), sync regional em paridade (42.797). **Produtos agosto = 188 linhas PRELIMINARES, todas de grupos só-cancelados (GMV 0, 1.162 cancelamentos)** — pedidos de 01–04/08 ainda não têm status 'Concluído'; a API corretamente mostra 0 produtos elegíveis no mês; os números vão amadurecer nos próximos exports (upsert idempotente). Sem sobreposição de julho (orders canônicos 100% agosto). Janeiro–julho preservados bytewise em todas as camadas. | Repetir a rotina quando chegarem novos exports (cobertura além de 04/08). | Não exigir 31 dias de agosto; Produtos ago preliminares por definição da métrica ('Concluído'). Dois `hook.cache.json` (artefato de ferramenta, 0 KB) encontrados dentro de `shopee/` — excluídos do lote; considerar limpeza manual. |
| Shopee julho — Raw/Silver/Gold/Daily | **CONCLUÍDO** | Lote de julho (batch `4631c7f3…`, 42 arquivos, 32 order_file_ids) fechado nas quatro camadas em 04/08: Raw committed/reconciliado, Silver reconciliada (153.815 orders + 160 stats + 369 ads), Gold regional committed em nova tentativa manual (run_id `jul04184842`, artifacts-dir curto `C:\shp_art`) — **3.408 linhas e R$ 7.617.662,26** de GMV na janela 01–31/07, backup+`.sha256` íntegros e receipt publicado. Daily julho publicado no Neon (orders/shop-stats/ads, 155 linhas cada, `source_sync_run=success`): **5 marcas × 31 dias, GMV shop-stats R$ 7.311.536,51, 124.790 pedidos, ad_spend R$ 377.106,32, zero duplicidade**. Sync regional propagado (paridade Data Mart×Neon = 41.363). API de produção exibindo Shopee em jun **e** jul (`/monthly` e `/daily`). Junho, ML e TikTok intactos. | — | **Produtos Shopee jun/jul CONCLUÍDOS em 05/08/2026** — resultado geral Shopee junho/julho passa de PARTIAL para **SUCCESS**. Correção permanente do Bug 5 versionada (commit `21980a8`: `_collapse_variation_collisions` na Fase A + guarda `_assert_unique_keys` na Fase B) e executada via caminho oficial: XLSX (root isolado jun+jul, 59 arquivos Order) → `apps/api/etl/load_shopee_products.py` (PG local) → `sync_produtos --source shopee --full` → Neon. **972 linhas** dos dois meses (jun 470, GMV R$ 6.521.807,04; jul 502, GMV R$ 5.512.907,44), local×Neon **bytewise idênticos** nas colunas de negócio (3.443 linhas), histórico jan–mai preservado (2.471 linhas inalteradas), `audit.source_sync_run=success` (3.443/3.443), monitor Bug 8 aprovado (invariantes + 10/10 combinações marca×mês reconciliadas contra a fonte isolada), API de produção validada (`/produtos/shopee` e `/summary` — jun e jul disponíveis, 5 marcas, Pareto ok, ML/TikTok sem regressão). |
| Operação manual Shopee | **OPERACIONAL** | Primeiro ciclo novo ponta a ponta validado: 37 arquivos na Raw, Silver reconciliada integralmente e Gold executada com backup e receipt. | Transferir a rotina documentada e acompanhar as primeiras execuções externas. | API oficial Shopee ainda indisponível. |
| Gold regional e sync Neon | **CONCLUÍDO** | Data Mart e Neon em paridade contínua: 37.282 linhas em 23/07, atualizado automaticamente para 37.851 linhas na execução agendada de 24/07 (novo dado ML incremental do dia); em 04/08 o sync manual condicional levou o Neon a **41.363 linhas** em paridade exata com o Data Mart (por marketplace: mkt2=19.877, mkt3=21.486) após o fechamento de julho, com backup automático `..._backup_20260804_190456`. Sync regional agora roda dentro do `full_daily` recorrente, com backup automático a cada execução com divergência real. | Acompanhar a cadência diária dentro do `full_daily`. | Nenhuma. |
| Automação diária ML/TikTok | **ATIVO** | Task Scheduler `mktplace_full_daily` habilitado em 23/07. Primeira execução agendada real (24/07, 06:00) concluiu sozinha, sem intervenção: sete steps (`daily_ml`, `daily_tiktok`, `gold_regional_incremental`, `sync_region_if_needed`, `sync_produtos_ml`, `sync_produtos_tiktok`, `health_check`) todos `SUCCESS`, `STATUS GERAL: OK`, `ok_critical=true`, zero steps Shopee, lock liberado, logs preservados. Próxima execução: 25/07 06:00. | Observar as próximas execuções diárias e tratar a dívida de encoding (`UnicodeEncodeError` do logger) quando priorizado. | Horário 06:00 mantido por autorização explícita, ainda sem histórico de múltiplos dias. |
| API oficial Shopee | **PAUSADO** | Ainda usamos exports manuais. Criação dos apps por marca pausada em 24/07 para priorizar o Revamp de UI/UX. | Retomar criação e aprovação dos apps por marca no Console Shopee quando repriorizado. | Acessos, aprovação e configuração por marca. |
| Revamp UI/UX (Torre) | **PUBLICADO — GO COM RESTRIÇÃO** | **Publicado na Vercel:** commit `9fcf72a` em Production **Ready**; domínio canônico `https://mktplace-gobeaute.vercel.app` (200 nas 11 rotas), `mktplace-blond.vercel.app` como alias/redirecionamento; bundle apontando para o backend público `mktplace-api.onrender.com` (sem `localhost`/IP local); API online e CORS correto para o domínio canônico. O fallback "API offline" observado ocorria só na URL efêmera de deployment (SSO + Origin fora do CORS), não é falha do deployment nem de `NEXT_PUBLIC_API_URL`. Auditoria pós-deploy encerrada em 03/08/2026. Gates U0–U6 concluídos — ver histórico em [UI_REVAMP_PLAN.md](UI_REVAMP_PLAN.md). Gate U6 (QA integrado e fechamento) concluído em 28/07: QA visual executado em navegador (Playwright + Chromium temporários e isolados em `%TEMP%`, Torre na porta 3100) em desktop 1440×900, tablet 768×1024 e mobile 390×844 — 12 rotas carregam, drawer/drill-down/filtros→URL/links de marca/estados de erro validados; a rodada consolidada final corrigiu 2 findings: scroll horizontal interno na tabela "Performance por Marca" (via `TableScrollHint`, colunas antes cortadas no mobile) e eliminação do hydration error React #418 em Tempo Real (`clientReady` gating a data/hora do relógio no SSR). 376 testes, typecheck e build passando; nenhuma dependência nova; nenhum backend/pipeline/banco tocado; sem U6.1/U6.2. Gates U0–U4 concluídos e aprovados (24–25/07). Gate U5 (Qualidade, Tempo Real, Pedidos, Inteligência e Operações) implementado em 26/07: as 5 telas restantes ganharam cabeçalho/hierarquia consistente com U1–U4, navegação interna compacta, e o mesmo padrão `resolvedKey`/`display*` protegido por frescor de requisição. Decisão de filtros por tela replicada do escopo aprovado: Qualidade/Pedidos continuam herdando filtros globais; Tempo Real/Inteligência/Operações continuam independentes. Inteligência/Operações ganharam a guarda contra resposta obsoleta de retry que faltava (bug de wiring pré-existente). Rodada de correção consolidada em 28/07 resolveu 2 findings: (1) Tempo Real tinha dois relógios independentes (countdown exibido × `setInterval` de auto-refresh próprio) que podiam divergir após refresh manual/tentativa demorada — unificado num único agendamento: o countdown chegar a zero é agora o único gatilho do refresh automático, com `inFlightRef`/`mountedRef`/preservação de dado em falha preservados integralmente; (2) Pedidos com seleção exclusivamente Shopee exibia badge "API offline" e `aria-live` "dados carregados", confundindo ausência de cobertura com falha real — corrigido com indicador neutro e anúncio de acessibilidade específico, preservando o bloco de indisponibilidade e a ausência de fetch já existentes. Nenhuma métrica/rota/endpoint/regra de negócio alterada; nenhuma ação de escrita/pedido individual criada; nenhuma dependência nova. Filtros globais e contratos de API preservados; **376 testes, typecheck e build passando. Gate U6 concluído em 28/07/2026; publicado e auditado (GO COM RESTRIÇÃO) em 03/08/2026.** | Gate G1 (Gerencial drill-down-driven) concluído em 04/08 (commit `f5394d4`). **Gate G2 — arquitetura transversal de drill-down: Task 2 (implementação) concluída em 05/08** — 4 primitives de composição extraídos (`DrilldownContextLine`/`EvidenceRow`/`DataQualityNote`/`DrilldownCta`; `DrilldownMetricPair` descartado por falta de 2º consumidor), Canais como primeira aplicação completa do contrato (diagnóstico humano + sinais explicados via `channel-signal-reasons.ts`, referências do mesmo canal, p75 inclusivo), KPI com referência vs período anterior (só GMV, dado já carregado) e Insight com `refreshed_at` da resposta fresca do executive-summary. Zero endpoint/fetch/métrica nova; um único shell de diálogo; 412 testes, typecheck e build verdes. **Task 3 (QA visual + rodada única) concluída em 05/08: Gate G2 CONCLUÍDO tecnicamente** — QA em navegador (Playwright/Chromium temporários em `%TEMP%`, build de produção na porta 3100, API pública read-only) em desktop 1440×900 e mobile 390×844 nos 3 fluxos (Gerencial→KPI, Gerencial→Pulso, Canais→Detalhe→Marca): 0 erro de console/hydration, 0 host inesperado, foco/Escape/backdrop OK, filtros preservados ponta a ponta, 1 único shell. Rodada única = 3 findings semânticos (explicação completa de `ads_subutilizado` espelhando a regra real; inconsistência sinal×p75 dita explicitamente; próximo passo sem prometer o que a página de Marca não tem). 416 testes, typecheck e build verdes; zero dependência nova. Ver [DRILLDOWN_ARCHITECTURE.md](DRILLDOWN_ARCHITECTURE.md) §6–7. **G2 encerrado e versionado no commit `903aba0` ("feat(web): padroniza drilldowns da torre"), já em `origin/main`** — 15 arquivos, somente `apps/web/` e `docs/`, nenhum toque em backend/pipeline/banco. Estado preservado; nenhuma alteração de G2 nesta operação de backfill. | Restrições/decisões: o domínio canônico está público sem autenticação própria da Torre (definir modelo de acesso); smoke visual completo em produção não automatizado; corrigir manualmente o campo "About" do GitHub (`mktplace-one.vercel.app` → `mktplace-gobeaute.vercel.app`); não usar URLs efêmeras da Vercel como endereço da Torre. Dívidas não bloqueantes herdadas: U6-03 (3px em Pedidos no tablet), U6-04 (dois `<h1>`), 3 vulnerabilidades altas (`next`/`postcss`/`sharp`), ausência de teste automatizado de componente React. |
| Serving Data Mart × Neon | **PLANEJADO** | O Data Mart é a fonte analítica; o Neon continua como camada servida pela API. A decisão futura está registrada. | Comparar manutenção do Neon com uma camada `serving` direta no Data Mart. | Três canais precisam estar estáveis e automatizados primeiro. |
| Evolução da Torre | **CONTÍNUO** | API e painéis já refletem as principais correções publicadas. | Priorizar KPIs, visualizações e QA conforme as entregas de dados avançarem. | Alinhamento de produto e disponibilidade de dados confiáveis. |
| Octaprice e inteligência B2B | **PLANEJADO** | Escopo conceitual definido; nenhum desenvolvimento iniciado. | Fazer discovery da API, catálogo, vendedores, regras de preço e notificações. | Contrato da API e política comercial. |

## Ciclo ativo — fechamento operacional e automação

**Status: ENCERRADO em 24/07/2026.** Objetivo cumprido: sair de operações
manuais esporádicas para uma rotina diária automática de ML/TikTok/regional,
mantendo Shopee no fluxo manual já validado.

Sequência executada:

1. **Concluído em 23/07:** Gold regional Shopee de junho sincronizada; Data
   Mart e Neon reconciliados integralmente;
2. **Concluído em 23/07:** rodada manual observada do `full_daily` —
   `STATUS GERAL: OK`, `ok_critical=true`, zero steps Shopee, lock liberado,
   logs preservados;
3. **Concluído em 23/07:** Task Scheduler `mktplace_full_daily` habilitado
   (horário 06:00 mantido, demais parâmetros inalterados), por autorização
   explícita;
4. **Concluído em 24/07:** primeira execução agendada real observada às
   06:00 — concluiu sozinha, sem intervenção manual, `STATUS GERAL: OK`.

Critérios de encerramento:

- Data Mart e Neon regionais em paridade — **atendido** (mantido
  automaticamente pela execução agendada de 24/07);
- `full_daily` sem falha crítica e sem executar Shopee — **atendido** nas
  rodadas de 23/07 e 24/07;
- locks removidos e logs preservados — **atendido** nas duas rodadas;
- health check com `ok_critical=true` — **atendido** nas duas rodadas;
- Task Scheduler com horário e estado confirmados — **atendido**: habilitado,
  primeira execução real às 06:00 concluída com sucesso;
- nenhuma automação criada para `shopee_manual_refresh` — **atendido**.

Próximo marco (nova frente, fora deste ciclo): observar mais algumas
execuções diárias antes de considerar o horário 06:00 definitivamente
estável, e priorizar a dívida de encoding do logger quando conveniente.

## Roadmap por fase

### Fase 1 — Consistência e completude dos dados

**Status:** concluída na camada de dados, Neon e API.

Entregue:

- baseline reproduzível XLSX × Torre;
- correção e publicação de TikTok;
- correção e publicação de Shopee;
- análise semântica do Mercado Livre;
- comparador automatizado por canal, marca e mês;
- API refletindo os novos valores de TikTok e Shopee;
- backfill e publicação do Mercado Livre;
- reconciliação final das 70 células;
- API refletindo os valores finais dos três canais;
- redução do erro agregado de 7,05% para 1,3743%.

O QA visual do frontend segue na frente paralela de produto e não bloqueia
o encerramento da qualidade dos dados, já validada pela API.

Documento detalhado: [Reconciliação XLSX × Torre](analise_reconciliacao_xlsx_torre_jan_maio_2026.md).

### Fase 2 — APIs e automação dos três canais

**Status:** parcialmente implementada.

Já disponível:

- atualização recorrente de ML e TikTok;
- pipeline `full_daily` com controles operacionais;
- ingestão manual Shopee em Raw, Silver e Gold;
- health checks e alertas de frescor;
- sync regional Data Mart → Neon.

Falta:

- ativar o Scheduler após a rodada manual final;
- criar os apps oficiais Shopee por marca;
- substituir gradualmente scraping por API;
- decidir a arquitetura definitiva entre Data Mart e Neon.

Documentos detalhados:

- [Runbook da automação recorrente](runbook_sync_produtos.md)
- [Handoff operacional da Gold Shopee](shopee_gold_operacao_handoff.md)
- [Operação completa do Data Mart Shopee](shopee_datamart_operacao_completa.md)
- [Decisões de arquitetura](DECISIONS.md)

### Fase 3 — Octaprice e inteligência B2B

**Status:** planejada.

Escopo inicial:

- monitorar anúncios de vendedores B2B;
- detectar preços abaixo do acordado;
- criar notificações e fluxo de tratamento;
- reduzir falsos positivos com regras por produto, marca e vendedor;
- manter histórico auditável de avisos;
- gerar inteligência de presença, preço, concorrência e sell-out B2B.

O desenvolvimento começa somente após o discovery da API e a definição da política comercial.

### Frente paralela — visualização e produto

A evolução da Torre acompanha todas as fases. Novos painéis e indicadores devem ser priorizados somente quando a definição e a qualidade dos dados correspondentes estiverem aprovadas.

Referências:

- [Visão de produto](../PRODUCT.md)
- [Arquitetura](architecture.md)
- [Dicionário de KPIs](kpi_dictionary.md)

## Entregas recentes relevantes

- Gold regional criada no Data Mart e sincronizada com o Neon.
- Refresh regional incremental integrado ao fluxo recorrente.
- Shopee separada da automação diária e mantida como operação manual.
- Job auditável da Gold Shopee entregue com backup e receipt.
- Primeiro lote Shopee completo operado de ponta a ponta: Raw, Silver e Gold regional de junho reconciliadas para as cinco marcas.
- Gold regional Shopee de junho sincronizada ao Neon; Data Mart e Neon em paridade integral.
- Rodada manual observada do `full_daily` concluída (23/07): sete steps `SUCCESS`, `STATUS GERAL: OK`, `ok_critical=true`, zero Shopee.
- Task Scheduler `mktplace_full_daily` habilitado (23/07) e primeira execução agendada real concluída com sucesso (24/07, 06:00), sem intervenção manual.
- TikTok e Shopee reconciliados contra o XLSX e publicados na Torre.
- Mercado Livre recomposto, publicado e reconciliado; fase XLSX × Torre encerrada com erro agregado de 1,3743%.
- Backfill por janela exata implementado para evitar alterações fora do período aprovado.
- Decisão futura Data Mart × Neon registrada formalmente.
- Revamp de UI/UX (U0–U6) publicado na Vercel (commit `9fcf72a`, Production Ready) e auditado como GO COM RESTRIÇÃO: domínio canônico `https://mktplace-gobeaute.vercel.app` no ar, bundle apontando para o backend público e CORS validado (03/08/2026).

## Decisões pendentes

1. Definir a ordem de criação dos apps oficiais Shopee por marca.
2. Manter o Neon ou migrar a API para uma camada `serving` no Data Mart.
3. Definir política comercial, destinatários e escalonamento do Octaprice.
4. Priorizar as próximas visualizações e indicadores da Torre.
5. Definir o modelo de acesso/autenticação da Torre — o domínio canônico está publicamente acessível sem autenticação própria.
6. Corrigir manualmente o campo "About" do repositório GitHub, trocando `mktplace-one.vercel.app` (retorna `DEPLOYMENT_NOT_FOUND`) por `https://mktplace-gobeaute.vercel.app`.

## Ciclo ativo — evolução drill-down-driven da Gerencial

Status: **Gate G1 CONCLUÍDO (04/08/2026) — nova Gerencial drill-down-driven implementada, corrigida e validada em navegador; versionado no commit `f5394d4`.** A evolução transversal desse padrão (Gate G2 — contrato comum aos três conteúdos de detalhe e primeira aplicação completa em Canais) foi encerrada em 05/08/2026 e versionada no commit `903aba0` — ver [DRILLDOWN_ARCHITECTURE.md](DRILLDOWN_ARCHITECTURE.md).

Objetivo:
tornar a síntese executiva compacta, priorizada e explicável, com
drill-down antes da navegação.

Task 1 (design + auditoria) entregou, em [GERENCIAL_DRILLDOWN_PLAN.md](GERENCIAL_DRILLDOWN_PLAN.md): matriz da verdade de todos os alertas; **causa provada do "custo alto = 0,0%"** (falso positivo — sinal relativo `custo_alto` degenerando com custo TikTok somando 0 em 07/2026, `0 >= p75(0)` dispara para todas as marcas, texto incorreto); composição visual única (Pulso do período, KPIs no topo); regra determinística de categorização/agrupamento/prioridade; contrato mínimo aditivo do drill-down (sem endpoint novo); e o plano estreito da Task 2.

Task 2 (implementação) entregou: guardrail do `high_cost` (elimina o falso positivo 0,0% — exige custo atual > 0, > mediana e ≥ p75 do canal, sem threshold arbitrário e sem tocar os sinais de Canais); saúde comercial separada da confiança no dado (frescor/cobertura/`not_applicable` não afetam a saúde; `missing_data` força "indisponível"); Pulso do período (KPIs no topo, no máx. 3 insights agrupados/priorizados + "Ver todos", contagem separada de avisos de dado) com drill-down que explica antes de navegar (reusa o `KpiDrilldownDialog`); contrato estendido de forma aditiva (sem endpoint novo). 411 testes da API, 387 do web, typecheck e build verdes; detector mecânico sem findings.

Task 3 (correção consolidada + QA) corrigiu 8 findings: formatação orientada ao campo no drill-down (growth/drop com referência/delta em BRL, custo em p.p.); texto verdadeiro quando custo == p75 ("diferença vs p75: +0,0 p.p."); missing_data como "Dados indisponíveis" (nunca "Crítico", sem métrica "0" fabricada); evidência de stale_data (origem/última data/defasagem/limite) propagada ao drill-down; severidade de grupo = pior de todos os membros; CTA por membro em grupos multi-marca preservando querystring; `health.summary` só com variação de GMV (contagem vive no Pulso); acessibilidade (heading semântico, alvos ~44px, foco coerente). QA visual em navegador (Playwright temporário, executive-summary interceptado com payloads sintéticos): cenário normal, missing_data, falha isolada do resumo, diálogo individual + "Ver todos", CTA por membro, desktop/tablet/mobile, teclado/foco — tudo verde, sem overflow nem erros de console. 412 testes da API, 395 do web, typecheck, build e detector Impeccable verdes.

Sequência:
1. Gate G1 Task 1 — design e auditoria da verdade dos alertas — **concluída**;
2. Gate G1 Task 2 — implementação — **concluída**;
3. Gate G1 Task 3 — QA em navegador e correção consolidada — **concluída (PASS)**.

Limite:
máximo de três prompts; sem subgates G1.1/G1.2.

## Backfill ML e TikTok — 01/07 a 05/08/2026

Status: **CONCLUÍDO em 05/08/2026 (SUCCESS).** Frente independente do G2, que
permanece exatamente como foi encerrado (commit `903aba0`, em `origin/main`).

Problema: a Torre estava materialmente incompleta desde julho. O incremental
usa lookback de 3 dias e as execuções de 01/08 e 03/08 do `full_daily` foram
`BLOCKED` por indisponibilidade da VPN/Data Mart (`STATUS GERAL: FAILED` nos
dois dias). Como a execução seguinte só recarregou os dias recentes, os
buracos históricos nunca foram recuperados — e o health check baseado em
`MAX(data)` indicava dado "fresco" com julho incompleto.

Operação executada: uma única execução por canal de
`pipelines.ingestion.daily_performance --mode backfill --date-from 2026-07-01
--date-to 2026-08-05`, sem retry, sem alteração de código, de regra de GMV ou
de allowlist de status.

| Canal | Antes (janela) | Depois (janela) | Fonte | Cobertura julho |
|---|---|---|---|---|
| Mercado Livre | R$ 3.169.757,92 / 80 linhas / 20 dias | R$ 6.658.336,47 / 144 linhas / 36 dias | paridade R$ 0,00 | 31/31 dias × 4 marcas |
| TikTok Shop | R$ 3.640.542,16 / 85 linhas / 17 dias | R$ 10.461.442,13 / 180 linhas / 36 dias | paridade R$ 0,00 | 31/31 dias × 5 marcas |

Julho fechado: ML R$ 5.778.258,36 (era R$ 2.593.883,38, −55,1%); TikTok
R$ 9.454.502,44 (era R$ 3.365.849,64, −64,4%).

Validação: `audit.source_sync_run` #108 (ml, 144/144) e #109 (tiktok, 180/180)
com `status=success` e janela de fonte `2026-07-01..2026-08-05`; 14 checks de
qualidade `pass`; zero duplicidade na chave `(date, loja_id, marketplace_id)`;
reconciliação dia × marca em GMV, `orders`, `units_sold` e `unique_buyers` com
zero divergência sob a regra vigente do conector; API de produção em paridade
total com o Neon nas 9 combinações canal × marca. Hash agregado das linhas fora
da janela e de Shopee inalterado antes e depois das duas cargas.

Ressalvas:

- **Agosto é mês aberto.** O retrato é o do Data Mart em 05/08/2026 18:59 UTC
  (15:59 São Paulo); o dia corrente não está fechado e o Data Mart continua
  recebendo dados depois da execução das 06:00.
- **TikTok subestima os últimos dias por definição da regra.** Em 05/08 todos
  os pedidos da fonte estavam em `AWAITING_COLLECTION`, `AWAITING_SHIPMENT`,
  `UNPAID`, `ON_HOLD` ou `CANCELLED` — nenhum em `COMPLETED`/`DELIVERED`/
  `IN_TRANSIT` —, então o GMV do dia é R$ 0,00 legitimamente. A curva amadurece
  em 2–3 dias (01/08 R$ 327 mil → 04/08 R$ 99 mil → 05/08 R$ 0). São 12.481
  pedidos fora da allowlist conhecida em jul+ago, deliberadamente excluídos.
- **Junho não foi reprocessado** (fora da janela autorizada) e segue divergente
  da fonte recalculada hoje: TikTok Neon ~R$ 597 mil acima, ML ~R$ 13 mil
  acima. É diferença de fotografia de carga, não lacuna de cobertura — junho
  tem 100% dos dias nos dois canais. Reprocessar exige autorização própria.
- `audit.source_sync_run` #90 (`tiktok_daily`, 26/07) segue com
  `status=running` órfão, resíduo da execução interrompida. Não é lock e não
  bloqueia nada.

**Junho reprocessado e encerrado (05/08/2026).** Em execução única por canal na
janela 01–30/06, ML passou de R$ 4.581.094,67 para R$ 4.567.893,85 e TikTok de
R$ 9.658.673,45 para R$ 9.060.774,62 — ambos em paridade R$ 0,00 com o Data Mart
em GMV, `orders`, `units_sold`, `unique_buyers` e `canceled_orders`, 30/30 dias
por marca, `source_sync_run` #110 e #111 `success`. Com isso a **recuperação
histórica de ML e TikTok está encerrada**: junho, julho e 01–05/08 reconciliam
com a fonte e com a API de produção. Fingerprint por canal/mês comprovou
alteração restrita a `mkt2_2026-06` e `mkt1_2026-06` (24 de 26 buckets
inalterados).

Próxima frente: **automação server-side do consumo do Data Mart** no
**repositório corporativo do Airflow**, substituindo o Windows Task Scheduler
local — janela fechada idempotente e health check por cobertura (não por
`MAX(data)`). Frente escolhida e **pausada até o fechamento do Gate DQ1**.

## Gate DQ1 — qualidade de dados dos três marketplaces

Status: **CONCLUÍDO em 05/08/2026** (auditoria read-only e documental, rodada
única, sem escrita, pipeline, deploy ou commit). Detalhes, números e
classificação completa em
[MARKETPLACE_DATA_QUALITY_CHECKPOINT.md](MARKETPLACE_DATA_QUALITY_CHECKPOINT.md).

Cadeia confirmada: a Torre **não** chama as APIs dos canais — elas alimentam o
Data Mart a montante e a Torre consome o Data Mart (Shopee segue via export
manual).

Veredito: **ML, TikTok e Shopee = TRUSTED WITH LIMITATION**; **Regiões = NOT
TRUSTED como GMV comparável entre canais**. Cobertura jan–jul integral nos três
canais (única ausência, ML/barbours em 20/01, é ausência real na fonte); Neon ×
API em paridade R$ 0,00 em `/daily`, `/overview`, `/monthly`, `/canais` e
`/quality`; zero duplicidade e zero nulo de chave.

Dois bloqueadores abertos: (1) **TikTok não tem cancelamento nem devolução em
nenhum ponto servido** — `gold.tiktok_brand_daily.canceled/returned/refunded`
valem 0 em todas as 1.080 linhas de 2026 contra 436.814 pedidos `CANCELLED` na
Raw, e a ausência não é declarada como `not_applicable`; (2) **Regiões mede
43,8% menos que Gerencial/Canais no mesmo período** (julho: R$ 12,67M vs
R$ 22,54M) e reporta `uf_fill_pct: 100%` / `coverage_level: ok`.

Achado estrutural novo: a fonte reconstrói o estado *atual* dos pedidos, então
**um mês fechado reconcilia apenas no instante da carga e deriva depois** —
julho divergiu R$ 155,90 (ML) e R$ 37,54 (TikTok) em ~1h após a própria carga.
Os resíduos de jan–mai são deriva acumulada desde 22/07, não erro de pipeline.

Nenhuma correção foi aplicada. As 6 correções necessárias e os 7 requisitos que
o DQ1 estabelece para a migração ao Airflow estão no checkpoint.

## Próximas prioridades

1. Observar as próximas execuções diárias do `full_daily` agendado antes de considerar o horário 06:00 definitivamente estável.
2. Transferir a rotina manual Shopee e iniciar a configuração administrativa da API oficial.
3. Priorizar o próximo ciclo de visualizações e QA da Torre.
4. Fazer discovery do Octaprice em paralelo, sem iniciar implementação prematura.

## Fora do foco atual

Estão registrados como backlog, mas não são frentes ativas agora:

- relatório financeiro completo por canal;
- revisão das telas legadas;
- loader de metas;
- novos alertas de anomalia;
- administração de mapeamentos;
- CI/CD e containerização;
- forecast e projeções.

Fonte: [Backlog técnico](backlog.md).

## Regra de atualização

Este documento deve ser atualizado somente quando ocorrer pelo menos um dos eventos abaixo:

- uma entrega relevante for concluída;
- uma frente mudar de status;
- surgir ou for removido um bloqueio importante;
- uma decisão arquitetural ou de produto for tomada;
- a ordem das próximas prioridades mudar.

Não registrar aqui cada gate, teste ou correção pequena. Esses detalhes pertencem aos runbooks, análises e documentos técnicos específicos.
