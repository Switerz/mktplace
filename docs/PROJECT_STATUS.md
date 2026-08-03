# Status geral — Torre de Controle de Marketplaces

**Última atualização:** 03/08/2026
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
| Operação manual Shopee | **OPERACIONAL** | Primeiro ciclo novo ponta a ponta validado: 37 arquivos na Raw, Silver reconciliada integralmente e Gold executada com backup e receipt. | Transferir a rotina documentada e acompanhar as primeiras execuções externas. | API oficial Shopee ainda indisponível. |
| Gold regional e sync Neon | **CONCLUÍDO** | Data Mart e Neon em paridade contínua: 37.282 linhas em 23/07, atualizado automaticamente para 37.851 linhas na execução agendada de 24/07 (novo dado ML incremental do dia). Sync regional agora roda dentro do `full_daily` recorrente, com backup automático a cada execução com divergência real. | Acompanhar a cadência diária dentro do `full_daily`. | Nenhuma. |
| Automação diária ML/TikTok | **ATIVO** | Task Scheduler `mktplace_full_daily` habilitado em 23/07. Primeira execução agendada real (24/07, 06:00) concluiu sozinha, sem intervenção: sete steps (`daily_ml`, `daily_tiktok`, `gold_regional_incremental`, `sync_region_if_needed`, `sync_produtos_ml`, `sync_produtos_tiktok`, `health_check`) todos `SUCCESS`, `STATUS GERAL: OK`, `ok_critical=true`, zero steps Shopee, lock liberado, logs preservados. Próxima execução: 25/07 06:00. | Observar as próximas execuções diárias e tratar a dívida de encoding (`UnicodeEncodeError` do logger) quando priorizado. | Horário 06:00 mantido por autorização explícita, ainda sem histórico de múltiplos dias. |
| API oficial Shopee | **PAUSADO** | Ainda usamos exports manuais. Criação dos apps por marca pausada em 24/07 para priorizar o Revamp de UI/UX. | Retomar criação e aprovação dos apps por marca no Console Shopee quando repriorizado. | Acessos, aprovação e configuração por marca. |
| Revamp UI/UX (Torre) | **PUBLICADO — GO COM RESTRIÇÃO** | **Publicado na Vercel:** commit `9fcf72a` em Production **Ready**; domínio canônico `https://mktplace-gobeaute.vercel.app` (200 nas 11 rotas), `mktplace-blond.vercel.app` como alias/redirecionamento; bundle apontando para o backend público `mktplace-api.onrender.com` (sem `localhost`/IP local); API online e CORS correto para o domínio canônico. O fallback "API offline" observado ocorria só na URL efêmera de deployment (SSO + Origin fora do CORS), não é falha do deployment nem de `NEXT_PUBLIC_API_URL`. Auditoria pós-deploy encerrada em 03/08/2026. Gates U0–U6 concluídos — ver histórico em [UI_REVAMP_PLAN.md](UI_REVAMP_PLAN.md). Gate U6 (QA integrado e fechamento) concluído em 28/07: QA visual executado em navegador (Playwright + Chromium temporários e isolados em `%TEMP%`, Torre na porta 3100) em desktop 1440×900, tablet 768×1024 e mobile 390×844 — 12 rotas carregam, drawer/drill-down/filtros→URL/links de marca/estados de erro validados; a rodada consolidada final corrigiu 2 findings: scroll horizontal interno na tabela "Performance por Marca" (via `TableScrollHint`, colunas antes cortadas no mobile) e eliminação do hydration error React #418 em Tempo Real (`clientReady` gating a data/hora do relógio no SSR). 376 testes, typecheck e build passando; nenhuma dependência nova; nenhum backend/pipeline/banco tocado; sem U6.1/U6.2. Gates U0–U4 concluídos e aprovados (24–25/07). Gate U5 (Qualidade, Tempo Real, Pedidos, Inteligência e Operações) implementado em 26/07: as 5 telas restantes ganharam cabeçalho/hierarquia consistente com U1–U4, navegação interna compacta, e o mesmo padrão `resolvedKey`/`display*` protegido por frescor de requisição. Decisão de filtros por tela replicada do escopo aprovado: Qualidade/Pedidos continuam herdando filtros globais; Tempo Real/Inteligência/Operações continuam independentes. Inteligência/Operações ganharam a guarda contra resposta obsoleta de retry que faltava (bug de wiring pré-existente). Rodada de correção consolidada em 28/07 resolveu 2 findings: (1) Tempo Real tinha dois relógios independentes (countdown exibido × `setInterval` de auto-refresh próprio) que podiam divergir após refresh manual/tentativa demorada — unificado num único agendamento: o countdown chegar a zero é agora o único gatilho do refresh automático, com `inFlightRef`/`mountedRef`/preservação de dado em falha preservados integralmente; (2) Pedidos com seleção exclusivamente Shopee exibia badge "API offline" e `aria-live` "dados carregados", confundindo ausência de cobertura com falha real — corrigido com indicador neutro e anúncio de acessibilidade específico, preservando o bloco de indisponibilidade e a ausência de fetch já existentes. Nenhuma métrica/rota/endpoint/regra de negócio alterada; nenhuma ação de escrita/pedido individual criada; nenhuma dependência nova. Filtros globais e contratos de API preservados; **376 testes, typecheck e build passando. Gate U6 concluído em 28/07/2026; publicado e auditado (GO COM RESTRIÇÃO) em 03/08/2026.** | Gate G1 — evolução drill-down-driven da Gerencial (ciclo separado, ainda não iniciado). | Restrições/decisões: o domínio canônico está público sem autenticação própria da Torre (definir modelo de acesso); smoke visual completo em produção não automatizado; corrigir manualmente o campo "About" do GitHub (`mktplace-one.vercel.app` → `mktplace-gobeaute.vercel.app`); não usar URLs efêmeras da Vercel como endereço da Torre. Dívidas não bloqueantes herdadas: U6-03 (3px em Pedidos no tablet), U6-04 (dois `<h1>`), 3 vulnerabilidades altas (`next`/`postcss`/`sharp`), ausência de teste automatizado de componente React. |
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

Status: PLANEJADO — aguardando Gate G1.

Objetivo:
tornar a síntese executiva compacta, priorizada e explicável, com
drill-down antes da navegação.

Sequência:
1. Gate G1 Task 1 — design e auditoria da verdade dos alertas;
2. Gate G1 Task 2 — implementação;
3. Gate G1 Task 3 — QA e única correção consolidada, se necessária.

Limite:
máximo de três prompts; sem subgates G1.1/G1.2.

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
