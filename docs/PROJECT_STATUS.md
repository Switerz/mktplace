# Status geral — Torre de Controle de Marketplaces

**Última atualização:** 17/08/2026 — **Checkpoint O1 Task 2/2: ponte de serving `IMPLEMENTADA E TESTADA, NAO EXECUTADA`.** Os tres syncs de serving passaram a existir como steps do `full_daily` (`serving_ml`, `serving_tiktok_brand`, `serving_tiktok_creator`), todos `critical=True`, cada um resolvendo a propria janela por `min(D-1, source_max)` — creator em D-2 nao rebaixa ML nem brand. **Nada rodou:** nenhum sync foi executado, nenhum banco escrito, **nenhum Scheduler criado ou alterado** e nenhum deploy feito. Afirmar que o serving ja se atualiza em producao seria falso. **Airflow continua inexistente.** A confiabilidade segue dependente de notebook ligado, usuario logado e VPN ativa, e o horario **06:00 continua divida** — em agosto so' 5 dos 17 dias tiveram execucao as 06:00. A TaskKey `serving_refresh` e' **contingencia manual**, nao agendada. Conclusao operacional depende de **piloto autorizado**. Contexto imediatamente anterior — **Gate S2 FECHADO: `PASS COM RESTRIÇÃO`. `/operacoes` está em produção lendo o Neon** (revisão `41eb171`, publicada manualmente no Render pelo proprietário): HTTP 200 em ~0,45 s, contra 500 em ~10,4 s antes. Serving cobre ML e TikTok brand até 16/08 e TikTok creator até 15/08 — 16/08 do creator é **ausência da fonte, nunca zero fabricado**. **O Gate G4 NÃO foi fechado:** `/inteligência`, `/tempo-real` e `/brand-detail` seguem 500 por dependência do Data Mart e são escopo do **Gate S3, não iniciado**. **Airflow continua inexistente e não comprovado**, e os syncs de serving **não estão no `full_daily`** — sem execução manual o serving volta a defasar. (ver **Checkpoint 17/08/2026** logo abaixo do resumo executivo). Contexto anterior — 11/08/2026 (**Frente ativa: camada de serving Data Mart → Neon — Gate S1 encerrado como `PARTIAL — PILOTO VALIDADO`**: migration `006` aplicada e `marts.fact_ml_gestao_diaria` criada, carregada e reconciliada; **execução dentro de worker Airflow ainda não comprovada** — é a razão do `PARTIAL`; **Gate S2 Task 3/3: `SUCCESS — BACKFILL COMPLETO E /OPERACOES PRONTO PARA VERSIONAMENTO`** — as três fatos de serving recarregadas até D-1 (12/08/2026) e reconciliadas, `/operacoes` com payload idêntico ao da Gold e provado sem Data Mart. **Ainda não versionado nem publicado.** Sanitização de erro endurecida nos dois módulos de serving: topologia não vaza mais em log. As **quatro** superfícies do G4 respondem 500 em produção — a medição de 10/08 que indicava três estava errada por janela de espera curta. **Revamp Visual V2 encerrado: `PUBLICADO — PASS WITH ISSUE`.** Fechamento publicado no commit `04d0d17` e validado por smoke read-only em 10/08/2026: 11 rotas em HTTP 200, sticky da Gerencial em `top=0px` nos três viewports, zero overflow, ticks semanais legíveis, comparação e diálogo aprovados, **zero regressão causada pelo release**. Restrição operacional aberta: as **quatro** superfícies do G4 (`/brand-detail`, `/tempo-real`, `/inteligencia`, `/operacoes`) sem fonte em produção — dependem do Data Mart, inalcançável do Render. Frente do Revamp **encerrada**; V2-0 a V2-4 em `c110e85`, `13c7ee0`, `e8f0630`, `2336567` e `04d0d17`.)
**Objetivo deste documento:** apresentar, em um único lugar, o estado das grandes frentes do projeto. Os detalhes técnicos, comandos e evidências continuam nos documentos específicos indicados em cada seção.

## Resumo executivo

A fase de **consistência e completude dos dados** foi concluída para janeiro a maio de 2026. TikTok, Shopee e Mercado Livre estão publicados no Neon e reconciliados com a referência XLSX; o erro agregado caiu de 7,05% para 1,3743%. Os resíduos restantes foram classificados como diferenças de fotografia, competência ou fonte manual, e não bloqueiam o encerramento.

Em paralelo, o primeiro ciclo completo do fluxo manual Shopee foi validado com dados de junho: Raw, Silver e Gold regional foram carregadas e reconciliadas para as cinco marcas. A transferência dessa rotina pode avançar enquanto a API oficial não estiver disponível. A sincronização regional de junho já foi concluída; uma rodada manual observada do `full_daily` também foi concluída com sucesso (`STATUS GERAL: OK`, sem executar Shopee). O Task Scheduler `mktplace_full_daily` foi habilitado em 23/07 e sua primeira execução agendada (24/07, 06:00) rodou automaticamente com sucesso — `STATUS GERAL: OK`, `ok_critical=true`, zero Shopee, sem intervenção manual. A automação diária de ML/TikTok/regional está, portanto, ativa e validada.

Uma nova frente foi aberta em 24/07: o **Revamp de UI/UX da Torre**. O Gate U0 (auditoria e especificação, sem implementação) foi concluído em 24/07, após uma rodada única de correção do roadmap — detalhes em [UI_REVAMP_PLAN.md](UI_REVAMP_PLAN.md). O Gate U1 (fundação visual e novo shell — sidebar clara/lavanda persistente no desktop, drawer mobile e topbar compartilhados) foi concluído e aprovado em 24/07, após uma rodada de correção de dois findings de revisão em runtime. O Gate U2 (Gerencial completa e padrão de drill-down) foi concluído em 24/07: a Gerencial foi reorganizada, os 4 KPIs principais ganharam drill-down agregado acessível (mesmo diálogo reutilizável) e um novo painel de desempenho por canal foi adicionado — tudo sobre os dados e contratos de API já existentes, sem alteração de backend/pipeline/banco. O Gate U3 (Canais e Marcas) foi concluído em 24/07, após uma rodada de implementação e uma rodada única de correção consolidada pré-commit: a página Canais foi reorganizada com navegação interna, resumos por canal e a matriz comparativa em destaque com um novo drill-down marca × canal (reaproveitando o mesmo diálogo do U2); a página de Marca ganhou link de volta preservando filtros e teve corrigido um fallback de modo demonstração que superestimava Pedidos/Ticket Médio sob seleção parcial de canal. Na rodada de correção, três findings foram resolvidos: Canais exibia dados antigos sob filtro/erro novo (corrigido com constantes `display*` protegidas por frescor), "Últimos 7 Dias" da página de marca reutilizava GMV combinado do mock sob seleção parcial de 2 canais (corrigido com uma projeção pura compartilhada entre gráfico e tabela), e o alvo de toque do botão "Detalhe" foi ampliado para 44×44px. O Gate U4 (Produtos, Regiões e Financeiro) foi implementado em 24/07 e passou por uma rodada consolidada de correção pré-commit (também 24/07), que resolveu 4 findings de revisão: (1) Produtos ganhou identidade de requisição própria por tabela e por resumo Pareto, em cada canal, corrigindo um frame de render anterior ao efeito em que a troca de aba/filtro podia mostrar dado/badge/escopo da identidade anterior; (2) Regiões e Financeiro separaram explicitamente os estados loading/error/fresh (antes um erro definitivo deixava skeleton/opacidade ligados como se ainda estivesse carregando); (3) Regiões passou a distinguir seção indisponível (`null`) de seção vazia com sucesso (`[]`), com um aviso compacto quando só uma parte dos dados falha, e Produtos ganhou o mesmo aviso quando exatamente tabela ou resumo falha; (4) a biblioteca `xlsx` (promovida a `dependency` na implementação original) foi **removida por completo** por ter vulnerabilidades de alta severidade sem correção disponível — a exportação de Produtos passou a gerar CSV (separador `;`, BOM UTF-8, proteção contra formula injection, sem nenhuma biblioteca). Um patch final estreito em 25/07 corrigiu um wiring incompleto do Finding 2: os caminhos de falha de Regiões e Financeiro atualizavam `error`/`loading` mas nunca concluíam `resolvedKey`, deixando a requisição atual presa em "loading" mesmo após um erro definitivo — corrigido com `setResolvedKey(key)` nos 3 pontos de falha, com regressão estática dedicada. As três páginas mantêm cabeçalho/hierarquia consistentes, `requestKey`/`resolvedKey` (com UF local incluída na identidade em Regiões), `RegioesBrazilMap` lazy via `next/dynamic`, e Financeiro com navegação marca→`/brand/[brand]` e `TableScrollHint` nas 3 tabelas. Filtros globais, contratos de API e regras de negócio preservados nos quatro gates; `npm audit --omit=dev` caiu de 4 para 3 vulnerabilidades altas — `next` é dependência **direta**, `postcss` e `sharp` são dependências **transitivas** relacionadas ao `next`; as três são pré-existentes e tratadas como dívida separada. O Gate U5 (Qualidade, Tempo Real, Pedidos, Inteligência e Operações) foi implementado em 26/07 numa única rodada: as 5 telas restantes ganharam o mesmo padrão de identidade de requisição do U4 (`resolvedKey`/`display*` protegidos por frescor), decisão de filtros por tela replicada do escopo aprovado (Qualidade/Pedidos herdam filtros globais; Tempo Real/Inteligência/Operações não herdam), cobertura Shopee isolada/combinada tratada explicitamente em Pedidos, e o polling de Tempo Real reescrito com uma máquina de 5 estados que nunca mais perde o último dado válido numa falha silenciosa nem sobrepõe requisições. 44 novos testes (359 no total), typecheck e build passando. Uma rodada de correção consolidada do U5 em 28/07 resolveu 2 findings de revisão: (1) Tempo Real tinha dois relógios independentes — o countdown exibido e um `setInterval` de auto-refresh próprio, criados separadamente — que podiam divergir após um refresh manual ou uma tentativa demorada (o texto mostrava um prazo que não correspondia ao próximo fetch real); corrigido unificando em uma única fonte de verdade: o countdown chegar a zero passou a ser o único gatilho do refresh automático; (2) Pedidos com seleção exclusivamente Shopee (sem cobertura nesta fonte) exibia badge "API offline" e anunciava "dados carregados" via `aria-live`, confundindo ausência de cobertura com falha de rede/sucesso; corrigido com um indicador neutro e uma mensagem de acessibilidade específica. 9 testes novos (368 no total), typecheck e build passando; nenhuma métrica/rota/dependência alterada; sem U5.1. **Gate U5 aprovado em 28/07/2026.** O **Gate U6 (QA integrado e fechamento) foi concluído em 28/07/2026**, encerrando o revamp: o QA visual pendente desde o U1 foi finalmente executado em navegador (Playwright + Chromium temporários e isolados em `%TEMP%`, Torre na porta 3100) em desktop/tablet/mobile — as 12 rotas, o drawer, os drill-downs, os filtros→URL e os estados de erro foram validados — e a rodada consolidada final corrigiu os 2 últimos findings necessários (scroll horizontal interno na tabela "Performance por Marca" e o hydration error React #418 em Tempo Real), com 376 testes, typecheck e build passando. **Gates U0–U6 concluídos.** Em 28/07 o commit `9fcf72a` foi publicado automaticamente na Vercel (integração GitHub→Vercel) e a **auditoria pós-deploy foi encerrada em 03/08/2026 como GO COM RESTRIÇÃO**: deployment Production **Ready** do commit `9fcf72a`, domínio canônico `https://mktplace-gobeaute.vercel.app` respondendo (200 nas 11 rotas), `https://mktplace-blond.vercel.app` como alias/redirecionamento, bundle apontando para o backend público (`mktplace-api.onrender.com`, sem `localhost`/IP local), API online (`openapi.json` 200) e CORS correto para o domínio canônico. O fallback "Demonstração · API offline" observado antes ocorria **apenas na URL efêmera de deployment** — atrás do SSO da Vercel e fora da allowlist de CORS do backend — e **não** representa falha do deployment nem erro de `NEXT_PUBLIC_API_URL`. Restrições em aberto: o domínio canônico está público sem autenticação própria da Torre (decisão de acesso pendente), o smoke visual completo em produção não foi automatizado, e o campo "About" do GitHub ainda aponta para a URL antiga (`mktplace-one.vercel.app`, que retorna `DEPLOYMENT_NOT_FOUND`). Em paralelo, a criação dos apps oficiais Shopee por marca foi pausada para priorizar essa frente.

## Checkpoint 17/08/2026 — operação de atualização ML/TikTok/Shopee até 16/08

**Resultado geral: `PARTIAL`.** Teto operacional D−1 = 16/08/2026 respeitado em
todas as camadas (0 linhas ≥ 17/08 nas seis tabelas tocadas). Nenhum retry de
escrita, nenhum commit/push, nenhuma correção de código.

**Publicado:**

- **ML Daily 01–16/08** (backfill de janela exata, 64 linhas) e **TikTok Daily
  01–16/08** (80 linhas). Divergência prévia era material — o TikTok estava
  desatualizado por não-maturação: 10/08 marcava R$ 9.741,23 contra
  R$ 369.846,24 reais; 09/08, R$ 188.955,98 contra R$ 373.741,75. Após a carga,
  **paridade exata com a fonte nos 32 pares dia × marketplace**. `source_sync_run`
  151/152 `success`.
- **Shopee Raw + Silver** do lote isolado 10–16/08: 10 arquivos (5 shop-stats,
  5 ads), 364 linhas, reconciliação Raw íntegra e Silver committed (40 + 324).
- **Shopee Daily**: GMV de shop-stats em **10–15/08** e ads em 10–16/08. O GMV de
  10/08 foi revisado de R$ 167.838,15 para **R$ 149.489,35** — shop-stats é a
  fonte autoritativa por contrato.
- **Produtos TikTok**: 14/08 corrigido de 675 para 876 linhas e 15/08 publicado.

**Não executado, com razão:**

- **Shopee orders (Raw/Silver/Gold/Daily)** — drift de conteúdo na origem: na
  parte 3/3 do export kokeshi, a coluna `Total global` vem com marcador textual
  em **todas as 5.473 linhas**. O transform Silver tem fail-fast nessa coluna
  (`order_grand_total: valor fora do formato/domínio esperado`), então carregar
  abortaria a transação e derrubaria junto as outras quatro marcas. Como a parte
  3/3 é justamente a que cobre 14–16/08, carregar só as partes 1 e 2 publicaria
  um GMV silenciosamente incompleto. **Requer novo export do kokeshi.**
- **Serving ML e TikTok (3 fatos)** — drift histórico anterior ao gap, o que pela
  regra da operação interrompe a etapa sem ampliar janela: `fact_ml_gestao_diaria`
  52 células em 17 datas desde 29/06; `fact_tiktok_brand_content_daily` 532
  células em 221 datas desde 06/10/2025 (colunas mais afetadas:
  `new_videos_posted`, `total_fees`); `fact_tiktok_creator_daily` 267 células em
  3 datas desde 12/08. Causa provável comum: revisão retroativa da fonte após a
  carga do serving — a investigar antes de qualquer novo backfill.
- **Produtos ML** — `gold.ml_produto_ranking` é ranking acumulado sem data de
  referência e **já incorpora 17/08** (13 produtos com `last_sale = 17/08`); o
  teto não pode ser respeitado sem alterar a query.
- **Gold regional incremental + sync** — `_ml_incremental_select` filtra só
  `date_created > min_date`, **sem teto superior**; o diagnose apontou 104 linhas
  novas incluindo 17/08. Gold e Neon regional já estão em paridade exata
  (mkt2 21.606 / R$ 33.474.947,57; mkt3 22.561 / R$ 37.666.242,46), então o sync
  seria no-op.
- **Produtos Shopee** — segue não isolável (`SHOPEE_ROOT` fixo no loader).

**Achados que exigem decisão:**

1. **`/api/v1/performance/operacoes` responde 500 em produção** (determinístico,
   2/2), enquanto `/overview`, `/canais`, `/regioes/summary` e os dois resumos de
   Produtos respondem 200. A mesma função roda **OK localmente contra o mesmo
   Neon**. O commit `861648a` (14/08) trocou `gold.*` por `marts.*` nesse
   endpoint; na revisão anterior o SQL contém `FROM gold.`, o que faz
   `_uses_datamart()` rotear para o Data Mart — inalcançável do Render — e
   levantar `RuntimeError`. **Hipótese: produção está numa revisão anterior a
   `861648a`; o deploy resolveria.** Não executado por não estar autorizado.
2. **Shopee 11–15/08 tem GMV e ads, mas `orders` nulo**, e **16/08 tem ads sem
   GMV** — o conector de ads só aceita `--days`, não janela exata, e rateia o
   total do período uniformemente (comportamento já vigente: 25–31/07 e 01–09/08
   seguem o mesmo padrão). Some quando o export do kokeshi for refeito.
3. O export shop-stats trouxe **16/08 zerado com 0 visitantes** — truncamento, não
   um zero real. Por isso a janela publicada parou em 15/08.

**Preservado:** resíduos Git intocados; `raw.ml_orders`/`gold.*` sem escrita;
runs órfãos 52 e 90 (julho) não tocados; backups anteriores não removidos.

### Gate SD2 Task 1/2 (17/08/2026) — `BLOCKED`, sem alteração de código

Auditoria read-only das três partes do lote kokeshi para decidir se o marcador
`err` podia ser suportado com segurança. **Não pôde.** Nenhuma linha de código,
schema ou SQL gerado foi alterada; nenhuma carga executada.

A hipótese de trabalho era que `err` estivesse restrito a `Total global`, campo
que o próprio contrato já registra como não-settlement. A medição refutou isso:
`err` está em **18 colunas**, todas sob o contrato numérico, em 100% das 5.473
linhas da parte 3/3 — incluindo `Valor Total`, comissão e serviço (bruta e
líquida), `Taxa de transação`, `Valor estimado do frete` e as taxas de envio.
As partes 1/3 e 2/3 estão limpas.

Dois motivos independentes tornam a flexibilização inviável no escopo previsto:

1. o GMV da **Gold regional** Shopee vem de `order_amount` (`Valor Total`), não
   de `Subtotal do produto` — converter para `NULL` produziria um GMV regional
   subestimado sem sinalização, porque `SUM` ignora NULL;
2. `total_settlement` é consumido na API por `COALESCE(SUM(...), 0)` em quatro
   superfícies, e há um `AVG(avg_settlement_pct)` que ignora NULLs — ausência
   viraria zero ou média parcial.

O que a auditoria também estabeleceu, e vale para a Task 2/2: as três partes têm
cabeçalho idêntico (65 colunas, assinatura `8621e8ad05d59e42`), formam partição
**disjunta** por pedido (14.844 pedidos, zero sobreposição), e a parte 3/3 é
insubstituível — **15/08 e 16/08 existem só nela** (2.371 e 2.681 linhas), além
de 421 linhas de 14/08. `Subtotal do produto` e `Quantidade` estão 100%
parseáveis nas três partes, assim como data, status e identificador.

**Encaminhamento: novo export da kokeshi.** É correção de origem, não de código
— o fail-fast atual se comportou como projetado. Detalhamento em
[staging_shopee_contract.md](staging_shopee_contract.md) §15.

### Operação SD2-A (17/08/2026) — `BLOCKED`, zero escritas consumidas

Tentativa de fechar as três fatos de serving até 16/08. **Parada no precheck de
cobertura, antes de qualquer escrita.** As três autorizações de escrita
permanecem intactas — nenhuma foi consumida.

| fonte Gold | MAX(date) | cobre 16/08? |
|---|---|---|
| `gold.ml_gestao_diaria` | 2026-08-16 | sim |
| `gold.tiktok_brand_daily` | 2026-08-15 | **não** |
| `gold.tiktok_creator_daily` | 2026-08-15 | **não** |

Dois gates independentes convergiram: o precheck de cobertura e o próprio
diagnose read-only dos syncs oficiais, que recusaram a janela com
`cobertura incompleta: 1 dia(s) sem linha` (exit 2) para brand e creator. O
diagnose do ML passou (exit 0). Publicar 16/08 para TikTok exigiria fabricar a
data — não foi feito.

As fontes estão íntegras e estáveis: fingerprints determinísticos idênticos em
duas amostragens separadas, zero duplicidade de chave, zero NaN, cobertura
diária contígua. O drift histórico segue medido e pendente — na janela até
16/08 o ML tem 1.645 linhas na fonte contra 1.637 no destino (Δ GMV
R$ 242.544,86); brand 1.571 contra 1.566; creator 187.848 contra 187.185.

**Estado do serving: inalterado** — 1.637 / 1.566 / 187.185 linhas, todas ainda
em `max(date) = 14/08`, com os `source_run_id` de 14–15/08 preservados.

**Quando destravar:** assim que `gold.tiktok_brand_daily` e
`gold.tiktok_creator_daily` alcançarem 16/08. O ML já está apto isoladamente
hoje — o diagnose passa limpo com `--date-from 2025-04-27 --date-to 2026-08-16`
— mas depende de autorização própria, já que a regra desta operação era parar
antes de qualquer escrita se qualquer fonte não cobrisse o teto.

Reconfirmados read-only, ambos agravados desde a última medição: Produtos ML
(ranking acumulado, 0 de 24 colunas com data de referência, agora **22** linhas
com `last_sale = 17/08`) e Regional (`raw.ml_orders` com **31** pedidos de
17/08 que o `_ml_incremental_select` arrastaria por não ter teto superior).
Nenhum dos dois foi tocado.

`/operacoes` valida localmente contra o Neon atual — cinco blocos do contrato
presentes, teto respeitado, 40 testes de contrato verdes — e segue **500 em
produção**, classificado como `861648a` ainda não publicado no Render. Só ficará
disponível após publicação separada do backend, fora do escopo desta operação.

Sem mudança de GMV, frete TikTok, elegibilidade, status ou regra comercial.
Shopee **não** está integralmente atualizada: orders/Gold/Daily-orders seguem
bloqueados pelo defeito de origem, e o Daily tem GMV só até 15/08. Airflow
continua não criado e não comprovado.

### Operação SD2-B (17/08/2026) — serving `SUCCESS`; Shopee auditada e liberada para plano

Duas trilhas independentes. A regra de corte deixou de ser acoplada: cada fonte
usa `effective_to = min(MAX(date) estável, 2026-08-16)`, e uma fonte atrasada
não bloqueia mais as outras.

**Trilha B — serving reconciliado. As três escritas foram executadas, uma
tentativa cada, todas com sucesso:**

| tabela | janela publicada | linhas | run_id |
|---|---|---|---|
| `fact_ml_gestao_diaria` | 2025-04-27 → **2026-08-16** | 1.637 → **1.645** | `sd2b-full-ml-20260816` |
| `fact_tiktok_brand_content_daily` | 2025-10-05 → **2026-08-15** | 1.566 → **1.571** | `sd2b-full-brand-20260815` |
| `fact_tiktok_creator_daily` | 2025-10-07 → **2026-08-15** | 187.185 → **187.848** | `sd2b-full-creator-20260815` |

O TikTok publica até 15/08 porque suas fontes Gold param aí — a data ausente
**não foi fabricada**. A data efetiva difere por fonte e é isso que a
documentação e a interface devem refletir.

**O drift histórico foi eliminado.** Reconciliação bidirecional independente
(conjunto de chaves + tupla, coluna a coluna, cross-database) devolveu `EXCEPT`
0/0 e **zero célula divergente** nas três — contra 52, 532 e 267 células
divergentes medidas antes. Cada tabela ficou com um único `source_run_id`
cobrindo 100% das linhas, `MAX(date)` exatamente igual ao seu `effective_to`,
zero duplicidade e zero linha acima de 16/08.

Isolamento provado: das 35 tabelas de `marts`, mudaram apenas as três
autorizadas. `fact_marketplace_daily_performance` (3.177),
`fact_marketplace_region_daily` (44.167), `fact_shopee_product_monthly` (3.631),
`fact_ml_produto_ranking` (1.647), `fact_tiktok_product_daily` (213.013) e os 18
backups permaneceram idênticos. Nenhuma tabela nova, nenhuma migration.

**Trilha A — novo export Kokeshi APROVADO na auditoria, sem nenhuma escrita
Shopee.** Os dois arquivos de 13–16/08 substituem a parcela corrompida:

- 65 colunas e assinatura de cabeçalho `8621e8ad05d59e42`, idênticas ao export
  anterior — sem drift estrutural;
- 5.453 + 3.818 linhas; partição interna disjunta (zero pedido compartilhado);
- **zero `err` e zero inválido** nas 18 colunas antes corrompidas e em todas as
  demais numéricas do contrato;
- cobrem 13/08 (1.975), 14/08 (2.244), 15/08 (2.371) e 16/08 (2.681);
- em 14/08 o novo traz 2.244 = 1.823 do A2/3 + 421 do A3/3 — a união exata;
- **`A3/3 exclusivo = 0`**: descartar o arquivo corrompido não perde pedido
  algum;
- nos 3.527 pedidos comuns com o export saudável anterior, zero status revisado
  e zero data divergente.

O arquivo corrompido `..._20260810_20260816_part_3_of_3.xlsx` permanece
registrado como evidência e **nunca** é candidato a carga.

**Risco identificado no plano de carga:** Raw/Silver/Gold toleram sobreposição,
porque a Gold deduplica por `DISTINCT ON (brand, order_id) ORDER BY file_id
DESC` com JOIN de volta (preserva pedidos multi-item) — o maior `file_id` vence.
Já o **Daily orders duplicaria**: `parse_brand` concatena todos os
`Order.all*.xlsx` da pasta e `_aggregate_daily` **soma** `subtotal` e `qty` por
linha, então os 3.527 pedidos compartilhados entre A2/3 e o novo export seriam
contados duas vezes em 13–14/08. Os campos order-level usam `max()` e não
duplicam. Conclusão: **roots disjuntos e duas execuções**, nunca um root único.

Ainda pendente para a Shopee: o shop-stats de 16/08 veio truncado (GMV 0,00 com
0 visitantes), então o **GMV Daily de 16/08 continua indisponível** mesmo com
Orders válido. Orders bom não autoriza promover `Subtotal do produto` a GMV
oficial do Daily — shop-stats segue a fonte autoritativa por contrato.

`/operacoes` valida localmente (5 blocos, teto respeitado, 40 testes de contrato
verdes) e segue **500 em produção**: `861648a` ainda não publicado no Render.
Produtos ML e Regional não foram tocados; seus bloqueios seguem registrados.

### Operação SD2-C (17/08/2026) — `BLOCKED` antes das escritas; patch do Daily entregue

Objetivo era fechar Shopee Orders 10–16/08 nas quatro camadas. **Nenhuma das
seis escritas autorizadas foi consumida.** O inventário obrigatório revelou que
o plano de carga estava incompleto, e a operação parou no ponto previsto pela
própria regra ("não amplie silenciosamente; pare antes do apply e reporte").

**Causa da parada:** não é só a Kokeshi que está pendente. A Raw de **todas as
cinco marcas** para em 10/08, e as outras quatro têm um Orders de 10–16/08 no
disco, ainda não carregado:

| marca | arquivo | linhas | pedidos | período | integridade |
|---|---|---|---|---|---|
| apice | `Order.all.20260810_20260816.xlsx` | 863 | 566 | 10–16/08 | `err`=0, inválidos=0 |
| barbours | `Order.all.order_creation_date.20260810_20260816.xlsx` | 2.412 | 2.191 | 10–16/08 | `err`=0, inválidos=0 |
| lescent | idem | 1.198 | 1.126 | 10–16/08 | `err`=0, inválidos=0 |
| rituaria | idem | 1.073 | 983 | 10–16/08 | `err`=0, inválidos=0 |

(apice tem 64 colunas — é o segundo template já registrado no contrato, não
drift.)

Carregar só a Kokeshi produziria uma **Gold regional Shopee com uma única marca
em 11–16/08**: o refresh por janela "substituiria TODA a janela (delete completo
+ insert do recálculo)", e o recálculo sai da Silver, que não teria as outras
quatro marcas nesses dias. Não haveria perda — a Gold hoje termina em 10/08 —
mas haveria publicação incompleta apresentada como fechamento.

**Entregue nesta operação (local, sem banco):** a correção do Daily Orders, que
era pré-requisito e é independente do bloqueio. `--source shopee` usava o
`UPSERT_SQL` completo; como o transform de Orders devolve `None` para o funil e
para os 8 campos de Ads e devolve o subtotal em `gmv`, uma execução isolada de
Orders **apagaria** o shop-stats de 10–15/08 e os Ads de 10–16/08 já publicados,
e **substituiria** o GMV autoritativo pelo subtotal dos pedidos. O novo
`PATCH_SHOPEE_ORDERS_SQL` restringe Orders às suas colunas; `gmv`, funil, Ads,
campos TikTok e metas ficam fora do UPDATE. Uma chave nova nasce com `gmv` NULL,
não zero. `unique_buyers` usa `COALESCE(existente, novo)` para não sobrescrever
o valor autoritativo do shop-stats. Detalhes em
[data_contracts.md](data_contracts.md) §6.

26 testes focais novos provam **comportamento**, não texto: um interpretador do
`DO UPDATE SET` aplica as atribuições reais sobre uma linha preexistente e
verifica que GMV, visitantes, conversão, compradores e os 8 campos de Ads
sobrevivem; `run()` é executado de verdade para provar que ML e TikTok continuam
no `UPSERT_SQL` e que cada `--source` Shopee usa o seu patch. Suíte total: 1.966
testes verdes.

**Falta para fechar a Shopee** (ordem sugerida, com o patch já no lugar): Raw dos
4 arquivos das outras marcas + Kokeshi A1/3 e A2/3, depois Kokeshi novo 1/2 e
2/2 (file_id maior vence 13–14/08); Silver; janela Gold derivada dos
`order_file_ids` das cinco marcas; Daily em dois roots disjuntos, cada um com as
cinco marcas. O arquivo `part_3_of_3` continua proibido — ausente da Raw (por
hash e por nome) e da Silver, confirmado nesta operação.

### Operação SD2-D (17/08/2026) — Shopee Orders 10–16/08 fechada: trilha `SUCCESS`, geral `PARTIAL`

As sete escritas autorizadas foram executadas, uma tentativa cada, **todas com
sucesso e sem retry**. Raw, Silver, Gold e Daily Orders estão reconciliados de
ponta a ponta.

**Raw — 8 arquivos, 25.334 linhas, 3 batches:**

| file_id | marca | linhas | batch |
|---|---|---|---|
| 274–277 | apice, barbours, lescent, rituaria | 863 / 2.412 / 1.198 / 1.073 | `304ebf3c` |
| 278–279 | kokeshi (export anterior saudável) | 5.178 / 5.339 | `91e3d985` |
| 280–281 | kokeshi (export substituto) | 5.453 / 3.818 | `41148c24` |

A ordem garantiu a precedência exigida: antigo termina em 279, novo começa em
280. O arquivo corrompido `part_3_of_3` ficou **ausente de tudo** — provado por
quatro ângulos: por nome (0), por hash (0), na Silver (0) e por conteúdo (zero
linhas com `err` em `Total global` e `Valor Total` em toda a Raw de orders).

**Silver — 25.334 linhas, 0 pendências, 0 órfãos, 0 NaN.** A dedup fez o que
devia: dos **3.527 pedidos sobrepostos** entre os dois exports Kokeshi, **3.527
foram vencidos pelo novo e 0 pelo antigo**, e nenhum pedido vencedor tem itens
espalhados por mais de um arquivo.

**Gold — janela derivada 10–16/08, não 11–16/08.** A hipótese de começar em
11/08 foi refutada por medição: **2.855 pedidos de 10/08 tiveram o vencedor
alterado** (revisados, não novos), porque os file_ids 274–281 são maiores que os
anteriores. O conjunto completo de vencedores da janela é exatamente
`[274..281]` — nenhum file_id antigo vence, então os 8 novos bastam para
reconstruir as cinco marcas. Resultado: 99 linhas deletadas → **727 inseridas**,
receipt `committed/ok`, backup de 432.233 bytes com SHA-256 conferido contra o
companion. Fora da janela **intacto** (22.462 linhas, R$ 37.497.838,71), junho
(3.213) e julho (3.408) idênticos.

**Daily — dois roots disjuntos, sem dupla contagem.** O Root A publicou Kokeshi
10–14/08 e o Root B sobrescreveu 13–16/08. Estado final da Kokeshi: 1.912,
2.025, 1.746, 1.663, **1.767**, 1.886, 2.298 = 11.297 pedidos. A prova de que
não houve soma: 13/08 ficou em 1.663 (uma soma daria 3.326) e 14/08 em 1.767
(uma soma daria 3.201). Os totais diários do Daily batem **exatamente** com a
Gold nos sete dias (2.508 / 2.701 / 2.302 / 2.246 / 2.306 / 2.513 / 3.009).

**O patch parcial funcionou como projetado.** GMV, visitantes, conversão,
compradores e os oito campos de Ads ficaram **numericamente idênticos** ao
snapshot anterior às escritas — R$ 149.489,35 em 10/08 até R$ 172.357,39 em
15/08, 133.762 a 171.106 visitantes, ad_spend 9.544,56 nos sete dias. Nenhuma
coluna de outra fonte foi zerada, e o GMV nunca foi contaminado pelo subtotal
dos pedidos.

**GMV de 16/08 continua indisponível.** O shop-stats daquele dia veio truncado e
não foi recarregado nesta operação. A linha de 16/08 tem Orders, taxas e frete
válidos, mas `gmv` **NULL** — e o campo por canal reflete isso honestamente:
`shopee_gmv: null` com `orders: 2298`. Não é venda zero.

**Achado aberto — representação/verdade da interface, não cosmético.** No
endpoint `/daily`, `shopee_gmv` vem corretamente `null` em 16/08, mas o agregado
`total_gmv` trata o canal indisponível como zero, devolvendo `0.0`. **Um total
agregado não pode parecer completo quando um canal selecionado está
indisponível** — quem lê o total conclui "vendeu zero" onde o correto é "não
sabemos". O problema **não foi introduzido pelo SD2** (é anterior a esta frente)
e **não bloqueia a carga**, que está reconciliada; mas é um defeito de verdade da
interface, não um detalhe de apresentação, e **deve ser tratado em gate
separado** — a decisão de contrato (propagar indisponibilidade no total, ou
distinguir "sem dado" de "zero" na resposta) não foi tomada aqui e nenhuma
alteração de API foi feita nesta operação.

**Isolamento provado.** Das tabelas do Neon, mudaram apenas as colunas Orders do
Daily Shopee. Permaneceram idênticas: `fact_marketplace_region_daily` (44.167,
max 15/08), `fact_ml_gestao_diaria` (1.645), `fact_ml_produto_ranking` (1.647),
`fact_shopee_product_monthly` (3.631), `fact_tiktok_brand_content_daily`
(1.571), `fact_tiktok_creator_daily` (187.848), `fact_tiktok_product_daily`
(213.013), e os GMVs de ML e TikTok no Daily.

**Restrições que mantêm o resultado geral em `PARTIAL`:** GMV Shopee de 16/08
ausente; Regional Neon não sincronizado (o mecanismo atual arrastaria ML de
17/08); `/operacoes` ainda 500 em produção, aguardando publicação do backend
`861648a`. Serving ML/TikTok não foi repetido. 1.967 testes verdes.

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

Nenhuma correção foi aplicada no DQ1. As 6 correções necessárias e os 7
requisitos que ele estabelece para a migração ao Airflow estão no checkpoint.

## Gate DQ2 — verdade da interface (TikTok, Regiões e sinais de Canais)

Status: **CONCLUÍDO e VERSIONADO em 05/08/2026** (uma implementação + uma
rodada consolidada; sem deploy). Escopo estritamente de
**representação**: nenhuma regra de GMV, allowlist de status, threshold de
negócio, endpoint, cálculo de cancelamento, dado, pipeline ou banco foi
alterado. Detalhes em
[MARKETPLACE_DATA_QUALITY_CHECKPOINT.md](MARKETPLACE_DATA_QUALITY_CHECKPOINT.md) §11.1.

Os dois bloqueadores do DQ1 deixaram de ser representações enganosas:

1. **TikTok em Qualidade** — a tela não exibia cancelamento/devolução do canal
   e a ausência nunca era declarada. Agora há cards explícitos com valor `N/D`
   ("Não disponível nesta fonte"), nota afirmando que ausência de dado **não é
   taxa zero** e `aria-live` comunicando a indisponibilidade. A API já
   devolvia `None` corretamente — nada no backend precisou mudar.
2. **Regiões** — as três dimensões passam a ser distintas na tela: cobertura de
   **canal** ("TikTok Shop fora do escopo"), **elegibilidade** (só pedidos
   elegíveis ao fato regional) e **preenchimento de UF** ("UF preenchida
   (elegíveis)"). O total virou "GMV com cobertura regional" e 100% de
   preenchimento passa a vir com ressalva explícita de que não é cobertura de
   100% do GMV. Nenhum percentual geral foi fabricado; seleção só-TikTok rende
   `not_applicable`. Contrato da API inalterado.

O achado 6 (`custo_alto` no TikTok) teve o **diagnóstico do DQ1 corrigido**: o
sinal depende do fee de marketplace (que o TikTok tem, com aviso de base), não
do dado de mídia — os dois sinais de julho eram legítimos e foram preservados.
O risco real, de degeneração quando a distribuição não tem dispersão, foi
fechado aplicando ao **produtor** a guarda já aprovada no Gate G1 (`custo > 0`
e `> mediana` e `>= p75`), sem threshold novo e sem regressão em ML/Shopee.

**Limitações estruturais mantidas:** cancelamento/devolução de TikTok continuam
inexistentes na cadeia servida (nada foi calculado da Raw) e Regiões continua
medindo escopo próprio, não comparável ao GMV total. **A duplicidade de
`silver.stg_shopee_shop_stats` (mai/jun) segue pendente e precisa ser resolvida
antes de a Silver Shopee ser adotada como fonte no Airflow.**

Com o DQ2 concluído, o **Airflow está tecnicamente desbloqueado no que depende
das correções de verdade da interface** — os requisitos de representação que o
DQ1 levantou estão atendidos. A frente **permanece pausada apenas por
priorização**: retomaremos primeiro a evolução de drill-down (Gate G3, ainda
não iniciado). Nada de Airflow foi criado, configurado ou executado.

Validação: API 435 testes, web 429 testes, typecheck e build verdes; QA visual
em navegador (Qualidade com TikTok isolado/combinado/ML, Regiões com
Todos/TikTok/ML, Canais com TikTok) em desktop 1440×900 e mobile 390×844, sem
erro de console/hydration e com querystring preservada.

## Gate G3 — página de Marca "chegando quente"

Status: **Gate G3 CONCLUÍDO tecnicamente em 06/08/2026 (Tasks 1–3) — aguardando
revisão/commit.** Task 1 (desenho) em 05/08; Task 2 (implementação) e Task 3 (QA
visual, veredito **PASS**) em 06/08. Nenhum deploy realizado. Registro completo
em [DRILLDOWN_ARCHITECTURE.md](DRILLDOWN_ARCHITECTURE.md) §8 (§8.7 = Task 2,
§8.8 = QA).

**QA visual (Task 3): PASS.** 10 verificações × 2 viewports (desktop 1440×900 e
mobile 390×844), com Playwright/Chromium temporários em `%TEMP%` e API pública
read-only: acesso direto sem banner; contexto válido com banner antes dos KPIs;
âncora `#marca-periodo` funcionando (inclusive por teclado); TikTok+`custo_alto`
sem âncora e com limitação declarada; combinações inválidas ignoradas em
silêncio; troca de marca/canal descartando o contexto; retorno a Canais com
filtros preservados e sem `ctx_*`; sidebar não propagando contexto; a11y OK.
**0 erro de console, 0 hydration, 0 overflow, 0 host inesperado.** Sob falha
total da API o banner permanece sem declarar frescor nem fabricar métrica.
**Nenhuma rodada de correção de aplicação foi necessária** — os findings dos
primeiros runs eram do próprio script de QA (`networkidle` que nunca estabiliza,
espera da matriz, filtro de canal multi-seleção e sidebar oculta no mobile).

**Dívida descoberta no QA (fora do escopo, pré-existente):** o endpoint
`GET /api/v1/performance/brand-detail` **não responde em produção** (timeout em
45–120s para as marcas/meses testados, contra ~0,4s de `/performance/daily`). A
página de Marca já o chamava antes do G3 — o gate não adicionou fetch algum. O
efeito é a seção "TikTok Shop — Inteligência (competência mensal)" não
completar; KPIs, gráfico, últimos 7 dias e o banner de chegada funcionam.
Corrigir exige backend, proibido neste gate.

**Backend do G1 + DQ2 publicado e verificado (06/08/2026).** O deploy manual no
Render foi concluído no commit `04493d5` e a verificação read-only pós-deploy
passou: `/canais` com TikTok em 01–05/08 retorna **0 sinais `custo_alto`** no
cenário `custo = mediana = p75 = 0` (antes 5/5) e `/executive-summary` traz os
**5 campos aditivos do G1**, sem a frase "…acima do usual". Achado colateral: o
G1 também nunca havia chegado à produção — os dois gates entraram juntos. O
bloqueador operacional que travava a Task 2 está **encerrado**.

**Task 2 implementada:** o CTA "Abrir visão completa da marca" (detalhe marca ×
canal) passa a anexar um contexto **allowlisted** (`ctx_from=canais`,
`ctx_signal`, `ctx_channel`, `ctx_brand`) e a página de Marca renderiza um bloco
compacto "Você chegou aqui por…" com o sinal em linguagem humana, canal,
período, retorno à evidência em Canais e — **somente quando a página realmente
evidencia o sinal** — um CTA de âncora. `ads_subutilizado` é o único sinal com
evidência real aqui (KPI de investimento do período); custo, frete, `sem_dado` e
ROAS declaram a limitação em vez de prometer métrica ausente. **Nenhum número
trafega na URL**, `ctx_*` fica fora de `FILTER_QUERY_KEYS` (a sidebar descarta o
contexto) e, sem contexto válido, a página é idêntica à atual. `ctx_from=gerencial`
**não** foi implementado por não existir produtor real — propagação transitiva
desde a Gerencial segue como dívida.

Uma rodada estreita de correção fechou dois pontos da revisão: (1) **validação de
compatibilidade sinal × canal** nos dois lados (parse e produtor) — como a URL é
entrada não confiável, o TikTok aceita apenas `custo_alto` e `sem_dado`,
espelhando a aplicabilidade do contrato (sem Ads e sem frete de seller no
canal), enquanto ML e Shopee aceitam os cinco; (2) **redação neutra de
`ads_subutilizado`** ("sinal de Ads subutilizado no canal"), porque a regra
também dispara com percentual de Ads ausente ou gasto zero — afirmar "abaixo da
mediana" não seria verdade em todos os ramos. Nenhuma mediana/percentual é
transportada ou recalculada. Zero endpoint, fetch, dependência, registry, modal
novo ou mudança de backend; **460 testes**, typecheck e build verdes.

**Precheck do DQ2 em produção (read-only) revelou o bloqueador:** o front do
DQ2 **está publicado** (Qualidade mostra `Cancelamento TK`/`N/D`/"nesta fonte";
Regiões usa "GMV com cobertura regional" e "UF preenchida", sem o rótulo
antigo), mas a **API do Render ainda roda a lógica pré-DQ2**. Prova
comportamental: em `/canais?channels=tiktok` na janela 01–05/08 o custo de
marketplace é **0,0% em todas as marcas** (mediana 0,0 · p75 0,0) e a API
**emite `custo_alto` para as 5** — exatamente o falso positivo que a guarda do
DQ2 elimina. Julho não discrimina as duas versões (distribuição com dispersão
real, sinais legítimos em ambas). Nenhum deploy/redeploy foi feito. **A Task 2
do G3 só deve começar depois de o backend do commit `04493d5` estar em
produção.**

Diagnóstico da jornada: o contexto é perdido **no `<Link>` do CTA** —
`mergeFilteredHref` transporta apenas `FILTER_QUERY_KEYS` mais o que o destino
traz (`brands`, `channels`), então a Marca recebe *o quê* (marca, canal,
período) e nunca *o porquê* (sinal, origem). A mesma regra é o que garante, sem
alteração alguma, que o contexto **não vaze** pela sidebar.

Contrato escolhido: **parâmetros explícitos e allowlisted na URL** (`ctx_from`,
`ctx_signal`, `ctx_channel`, `ctx_brand`), com a seção-alvo **derivada** do
sinal em vez de transportada. Nenhum valor monetário, percentual, referência ou
texto livre na querystring; o bloco de contexto **não exibe número algum** — a
URL nunca é fonte de verdade de métrica. Contexto inválido, marca ou canal
incompatível: ignorado em silêncio. Sem contexto: página idêntica à atual.
`FILTER_QUERY_KEYS` **não** é ampliada; zero endpoint, fetch, dependência,
registry ou estado global.

Escopo da Task 2 (quando desbloqueada): 1 módulo puro
(`brand-arrival-context.ts`), 1 componente pequeno reusando as primitives do G2
(`BrandArrivalBanner`), edições em `app/brand/[brand]/page.tsx` e no CTA de
`ChannelComparisonDialogContent`. Um mapa explícito declara quais sinais a
Marca **não** consegue evidenciar (custo, frete, cancelamento) para nunca
prometer métrica ausente.

## Gate G4 — timeout de `/brand-detail` (diagnóstico)

Status: **Gate G4 encerrado em 06/08/2026 como `GO COM RESTRIÇÃO` — aguardando
revisão/commit.** Task 1 (diagnóstico read-only) e Task 2 (mitigação fail-fast +
QA) concluídas. Nenhum deploy realizado. Evidência em
[DRILLDOWN_ARCHITECTURE.md](DRILLDOWN_ARCHITECTURE.md) §8.9 (diagnóstico) e §8.10
(mitigação).

**A mitigação NÃO restaura os dados.** Foi adicionado
`datamart_connect_timeout_seconds` (default **10s**, faixa 1–30 validada pelo
Pydantic) aplicado **exclusivamente** ao `datamart_engine`; o engine
principal/Neon segue criado exatamente como antes. O código está **implementado
e validado, mas a mitigação ainda NÃO está ativa em produção**: somente **após a
publicação do backend** as rotas servidas pelo Data Mart passarão a falhar em
aproximadamente **10s** em vez de 45–120s — espera que o frontend já representa
como indisponibilidade. **Até essa publicação, produção continua podendo esperar
45–120s**, e **mesmo depois dela os dados seguirão indisponíveis**.
**`/brand-detail`, `/tempo-real`, `/inteligencia` e `/operacoes` continuam sem
conteúdo do Data Mart em produção.** A correção definitiva depende da **decisão
de camada de serving**;
migrar/sincronizar essas fontes para o Neon deve ser tratado futuramente junto
da arquitetura do Airflow, sem ampliar o G4. Provas: falha em 4,99s contra host
não roteável (1 tentativa, sem retry, sem DSN em mensagem), `SELECT 1` OK no
Neon e OK em 2,09s no Data Mart via VPN (o timeout não impede conexão válida),
14 testes focais novos e 449 testes da API passando.

**Causa raiz confirmada: não é consulta lenta, plano, índice, view nem cold
start — é a ausência de conectividade entre o Render e o Data Mart (RDS).** As
5 consultas de `get_brand_detail` leem `gold.*`, então `_uses_datamart()` as
roteia para o `datamart_engine`, que aponta para o RDS AWS — e o RDS **exige
VPN** (`runbook_sync_produtos.md`), conectividade que o `DECISIONS.md` ainda
lista como critério de uma decisão futura. Em produção nenhuma consulta chega a
executar: o tempo é **100% tempo de conexão** (0 bytes recebidos).

Prova por separação, medida no mesmo instante: as 4 rotas que ainda usam o
`gold_service` (`/brand-detail`, `/tempo-real`, `/inteligencia`, `/operacoes`)
deram **timeout em 4/4**, enquanto todas as rotas migradas para o
`performance_service`/Neon responderam em **0,42–0,82s**. Com acesso ao Data
Mart, as 5 consultas rodam em **4,07s no total** (nenhuma estoura
`statement_timeout` de 20s), o que descarta o SQL como causa.

Impacto: o payload alimenta somente a seção "TikTok Shop — Inteligência
(competência mensal)" da página de Marca, que **já degrada isoladamente** ("Dados
mensais indisponíveis — API offline"); o custo real é o usuário **esperar 45–120s
antes de ver a indisponibilidade**. As telas Tempo Real, Inteligência e
Operações dependem das outras 3 rotas afetadas.

A mitigação escolhida — **falhar rápido** no caminho do Data Mart, com
`connect_timeout` curto e explícito — foi **implementada e validada na Task 2**
(ver acima e §8.10), e **depende de publicação do backend para produzir efeito em
produção**. Migrar `/brand-detail` para o Neon **não** era correção mínima: no
`marts` existe apenas `fact_tiktok_product_daily`, sem equivalentes de
`tiktok_brand_daily`, `tiktok_creator_daily` ou `v_channel_efficiency` — seria
frente de dados própria.

## Ciclo ativo — Revamp UI V2 (Gerencial flagship)

**Gate V2-0 (auditoria comparativa e blueprint): CONCLUÍDO — REVISADO, aguardando
versionamento (06/08/2026) — somente desenho, nenhuma linha de UI
implementada.** Uma rodada de correção consolidada foi aplicada na mesma data,
resolvendo 4 findings de revisão do blueprint e encerrando as duas decisões de
produto que estavam abertas. Aberto porque, apesar de U0–U6 e
G1–G4 terem entregado shell, acessibilidade, estados de dados, drill-downs e
confiabilidade, o resultado visual e analítico da Gerencial continua abaixo do
padrão esperado: área vazia sob o gráfico de tendência, baixa densidade útil e
pouca continuidade entre resumo, tendência, decomposição, ranking e ação.

Entregas do gate, em [UI_REVAMP_V2_PLAN.md](UI_REVAMP_V2_PLAN.md) (auditoria,
matriz de padrões, contratos de dados, sistema visual, roadmap) e
[GERENCIAL_V2_SPEC.md](GERENCIAL_V2_SPEC.md) (blueprint, wireframes, mapa de
drill-down):

- **Causa técnica da lacuna visual identificada e evidenciada.** O container do
  bloco analítico usa `items-start` (`apps/web/app/page.tsx:347`), o gráfico tem
  altura **fixa** (`ResponsiveContainer height={260}`) e ocupa `lg:row-span-2`,
  enquanto a coluna direita (Pulso + Canais) tem altura de conteúdo e soma mais
  que o gráfico. As linhas da grade passam a ser ditadas pela coluna direita, e
  `align-items: start` impede o item de esticar até sua área — sobram ~200–260px
  de faixa branca sob o gráfico. **Trocar `items-start` por `items-stretch` não
  resolve**: o item de grade é um wrapper vazio, o card interno não tem `h-full`
  e a altura do gráfico é pixel fixo — são três mudanças acopladas. A correção
  local entra no V2-1 como parte de um sistema de alturas, não como entrega
  isolada, porque preencher pixel não é preencher significado.
- **A referência (`torre_b2b`) resolve o mesmo layout sem truque:** nenhum
  `items-start`, o card **é** o item de grade e a área do gráfico é
  `flex-1 min-h-[18rem]`. Padrão adotado e transformado em regra normativa.
- **Achado que inverte a direção da comparação:** a referência tem **0**
  `role="dialog"`, **0** `aria-live` e **0** `aria-sort` em todo o repositório, e
  **0** `aria-`/`role=` na Gerencial. Nenhum padrão de interação dela pode ser
  copiado como está; nossa acessibilidade e nosso contrato de frescor
  (`requestKey`/`resolvedKey`) permanecem como alvo a preservar, não a substituir.
- **Viabilidade de dados classificada item a item:** o V2-1 **não cria endpoint**
  e **não altera backend, banco ou pipeline** — reutiliza os endpoints atuais do
  Neon. A redação "frontend-only" foi corrigida na revisão porque era imprecisa:
  o V2-1 **aumenta de forma controlada o número de chamadas** (até 3 de `/trend`)
  e por isso precisa coordenar frescor e **falha parcial** entre respostas,
  nunca declarando um bloco completo se uma fonte necessária falhar. Só duas
  melhorias pedem **extensão aditiva** de `/trend` (série comparativa e
  granularidade), que ficam no V2-2 — hoje o router não repassa `compare_period`,
  então `compare` é buscado e silenciosamente ignorado pelo gráfico. **Nenhuma
  read model nova.** Seguem indisponíveis: margem/CMV, devolução ML,
  cancelamento/devolução TikTok, grão transacional e tudo servido pelo Data Mart.
- **Recusa de comparabilidade sem lastro (reforçada na revisão).** O funil de 4
  etapas da referência foi rejeitado e, na revisão, **toda a composição monetária
  foi removida** — o contrato não oferece valor cancelado, valor devolvido nem
  GMV anterior às exclusões. O bloco passou a ser **"Saúde do volume por canal"**,
  com métricas independentes por canal, cada uma na sua unidade, sem segmentos
  mutuamente exclusivos. Os rótulos foram acertados na correção factual final:
  `ml_total_orders` é **"Pedidos considerados"** (`ml_orders + ml_canceled`), não
  "pedidos elegíveis"; em Shopee o total considerado é
  `shopee_orders + shopee_canceled_orders`; o TikTok exibe **"Pedidos
  registrados"**, sem inferir total considerado. **ML e Shopee usam a mesma
  fórmula de taxa** — `cancelados / (não cancelados + cancelados)` — e o
  frontend consome a taxa servida pelo endpoint, declara a definição no
  drill-down e **nunca recalcula com outro denominador**. Não há ranking
  competitivo de cancelamento entre canais, mas a justificativa é semântica e não
  aritmética: fonte, processo de captura e semântica operacional dos status
  diferem entre marketplaces (API, export manual, allowlist com maturação) e o
  TikTok não tem cobertura confiável — então cada canal é apresentado
  descritivamente. Devolvidos e taxa de devolução da Shopee são métricas
  independentes, nunca partição do total.
- **Ranking de produtos removido da Gerencial.** Produtos não têm escopo temporal
  uniforme (ML é acumulado atual; TikTok e Shopee são competência mensal), então
  um Top Produtos sob o período global compararia três janelas sob o mesmo
  rótulo. Substituído por **"Concentração por marca"** via `/brands` no mesmo
  período global; ranking de produtos fica registrado como evolução futura
  dependente de contrato temporal uniforme.
- **Tendência por canal com até três chamadas existentes:** `/trend` devolve série
  agregada sem dimensão de canal, então o frontend fará **uma chamada por canal
  selecionado (máximo 3)** com seleção unitária, somando as séries por bucket
  para o total, com reconciliação contra o agregado do mesmo escopo, canal sem
  dado distinguível de zero real, identidade de requisição incluindo métrica, e
  falha parcial nomeando o canal ausente sem fallback silencioso.
- **Faixa final de KPIs fechada:** GMV, Pedidos, Ticket Médio, Investimento em
  Ads e ROAS por canal. **Confiança no dado deixou de ser o quinto KPI** e passou
  a ser uma faixa horizontal compacta e clicável entre filtros e KPIs. **Delta
  somente em GMV** — Pedidos e Ticket exibem "Comparação indisponível", Ads e
  ROAS ficam sem delta, e ROAS não tem total consolidado, soma nem média entre
  canais.
- **Roadmap V2-0 → V2-3** com orçamento de uma correção consolidada por gate e 14
  critérios mensuráveis para o V2-1. O teto genérico de 96px de área vazia foi
  substituído, na revisão, por critérios verificáveis por bounding box: diferença
  de **no máximo 24px** entre o final visual do card de Evolução e do item
  composto Pulso+Canais em desktop e tablet, zero linha órfã por `row-span`,
  nenhum espaço entre irmãos acima do gap do grid, e reconhecimento de que área
  interna de gráfico e de estados vazio/erro/carregando não conta como área vazia.

Restrições do gate respeitadas: zero alteração em `apps/web` e `apps/api`, zero
dependência, zero endpoint, zero banco/pipeline/Scheduler/Airflow, zero deploy,
zero cópia de código da referência, `DESIGN.md` não tocado (mudanças necessárias
registradas para aplicação futura) e resíduos preexistentes preservados. Clone
temporário da referência criado fora do repositório e removido ao final. **V2-1
não foi aberto.**

Limitação declarada: **não houve validação visual em navegador**. As alturas do
diagnóstico foram derivadas das classes CSS, não medidas em runtime — a
verificação numérica é tarefa do QA do V2-3. A referência não pôde ser executada
(depende de Supabase e variáveis ausentes; criar credenciais está fora de
escopo), então sua análise é 100% de leitura de código.

### Gate V2-1 — reconstrução da Gerencial

**CONCLUÍDO, APROVADO E VERSIONADO em `13c7ee0` (07/08/2026), 33 arquivos.** A rodada principal
de implementação e a **única** rodada consolidada de correção foram executadas; o
orçamento do gate está esgotado. Somente a rota `/` e seus componentes/helpers
diretos foram tocados — nenhuma outra tela recebeu o novo design.

**A rodada de correção fechou nove findings de uma revisão estrita**, dois deles
funcionais e graves: (1) o ramo global de erro/vazio da página apagava evolução,
Pulso, matriz, movimentos e fila quando **só** o `/overview` falhava, contrariando
o próprio contrato de fontes independentes — o gate global foi removido e cada
bloco passou a responder apenas às suas fontes, com o `aria-live` nomeando o que
ficou indisponível; (2) o detalhe de um ponto da série imprimia números
**mockados** com uma nota de demonstração, e agora, em página live, resposta
não-live é indisponibilidade, com `overview` e `brands` do recorte em estados
separados. Fecharam também: os quatro caminhos de drill-down que faltavam
(legenda de canal com isolamento da série, cabeçalho de canal da matriz,
concentração por marca explicando antes de navegar, e chips de sinal na célula);
o estado parcial da matriz quando `/canais` falha mas `/brands` está fresco; o CTA
do bucket mensal, que dizia "fixar este dia" e reduzia o mês a um dia; a nota de
cobertura de Ads, que citava Mercado Livre e Shopee mesmo com um só selecionado; e
o fechamento do diálogo por mudança de filtro vinda de back/forward ou URL colada.

A **faixa de confiança ganhou semântica honesta**: deixou de inferir "cobertura" a
partir de `gmv != null` e passou a reportar **disponibilidade de série** em quatro
estados distintos — verificando, disponível (inclusive com registros de valor
zero), sem registros e indisponível. Defasagem e avisos continuam vindo do
`/executive-summary` em separado; se ele não responder, a faixa mantém a
disponibilidade e declara que defasagem e avisos **não foram verificados**, em vez
de afirmar que não existem.

O QA da rodada encontrou um achado adicional que a revisão não previa: decidir o
modo demonstração apenas pelo `/overview` exibia KPIs mockados ao lado de matriz e
evolução reais. A regra passou a exigir que **todas** as fontes com fallback
tenham caído para mock.

Uma **reparação final de stop-loss** fechou cinco inconsistências da própria rodada
de correção, antes do commit. A mais relevante: a regra de modo demonstração ainda
podia ativar com **uma única fonte** concluída em mock, porque `every` sobre uma
lista filtrada é vacuamente verdadeiro — e a lista de séries incluía canais fora da
seleção e chaves de requisições antigas. A decisão foi extraída para um módulo puro
(`lib/gerencial/demo-mode.ts`) que exige o conjunto esperado da requisição atual e
devolve também um estado *pendente*, no qual uma fonte mockada fica em carregamento
neutro em vez de exibir números. Uma **correção terminal** fechou o último bug dessa
regra: um erro era tratado como espera, então o estado pendente nunca terminava —
os mocks das outras fontes ficavam presos em carregamento e a interface podia
permanecer em "Atualizando…" indefinidamente. Erro passou a ser uma conclusão
(`terminal_error`): a demonstração já não pode ser confirmada, as fontes em mock
viram indisponíveis e o carregamento encerra. Um erro com chave de requisição antiga
não é terminal para a requisição nova. Fecharam também: as quatro causas distintas de uma
célula da matriz sem linha comparativa, que antes recebiam a mesma frase de "falha
de carga"; o fallback de `signalLabel`, que devolvia o identificador `snake_case`
cru e podia vazá-lo para a interface de Canais; os `tick={{ fontSize: 11 }}` do
Recharts, que escaparam da varredura de tipografia por serem estilo inline; e a
contagem documental de drill-downs, agora **16 tipos de acionamento** com o critério
explicado.

Entregue: os oito blocos do blueprint (faixa de confiança, cinco KPIs, Evolução
dominante, Pulso+Canais, Saúde do volume por canal, Matriz Marca × Canal,
Movimentos + Concentração por marca, Fila de atenção), com a lógica de negócio em
sete módulos puros (`src/lib/gerencial/*`) testáveis sem React, um hook de
coordenação das seis fontes (`useGerencialSources`) e dez componentes de bloco. O
`page.tsx` ficou como coordenador (494 linhas), sem regra de negócio no JSX.

**A lacuna visual foi eliminada por construção e medida em runtime:** a diferença
entre o final visual do card de Evolução e do item Pulso+Canais é de **0px** nos
oito casos de desktop e tablet (com um e com três canais), contra o critério de
≤24px; zero overflow horizontal nos três viewports; o `row-span` e o
`items-start` que causavam a faixa órfã não existem mais.

O alerta hard-coded de Lescent saiu do JSX: a fila de atenção passou a ser
alimentada pelo `/executive-summary`, com listas separadas para risco comercial e
confiança no dado (avisos de dado têm escala própria, sem "Crítico" comercial).

**Três achados do próprio QA, corrigidos na rodada:** (1) os fetchers não
rejeitam — sem API devolvem mock com `live: false` — e um `/quality` em falha
renderizava cancelamento mockado ao lado de KPIs reais; agora, fora do modo
demonstração, `live: false` é indisponibilidade da fonte; (2) 5px de overflow
horizontal no mobile, causados por um `<button>` de KPI que se dimensionava pelo
conteúdo; (3) o `recharts` estava no bundle inicial porque os cards importavam as
cores de canal do módulo do gráfico, anulando o `next/dynamic` — First Load da
rota `/` caiu de **252 kB para 144 kB**.

Validações após a correção: **520 testes** (56 na suíte do V2), typecheck e build
verdes; detector do Impeccable sem findings nos 12 arquivos visuais do V2; QA em
navegador nos três viewports com nove cenários, incluindo falha isolada de
`/overview`, de `/canais` e de uma série de `/trend`, detalhe do ponto com uma
fonte indisponível, período longo em grão mensal, e navegação **por teclado** nos
quatro caminhos de drill-down novos — **zero falhas, zero erro de console ou
hydration, zero host inesperado, zero overflow horizontal**. Diferença entre o
card de Evolução e o item Pulso+Canais: **0px** nos quatro casos de desktop e
tablet, contra o critério de ≤24px. First Load da rota `/`: 147 kB, com o
`recharts` fora do bundle inicial. Zero backend, zero endpoint novo, zero
dependência (`package-lock.json` intocado), zero banco/pipeline/Scheduler/Airflow,
zero deploy, zero commit.

Limitações declaradas: `/executive-summary` responde em ~2,9s contra ~0,7s das
outras fontes, o que é justamente o motivo dos estados independentes; **no
encerramento do V2-1** a série comparativa e a granularidade selecionável seguiam
indisponíveis e declaradas em texto (as duas foram entregues depois, na Task 1/2 do
V2-2 — ver a seção seguinte); dois itens do spec não foram implementados
e estão registrados em §14.5 do [GERENCIAL_V2_SPEC.md](GERENCIAL_V2_SPEC.md).
Dívidas que **não** foram tocadas por estarem fora do escopo do gate: os dois
`<h1>` da página (U6-04, do shell), três componentes que ficaram obsoletos na
Gerencial mas são asseverados por testes estáticos preexistentes, e as fontes
abaixo de 12px nas outras dez rotas (16 arquivos com 11px, 22 com 10px, 3 com
9px). A rampa do `DESIGN.md` continua sem passo de anotação densa e sem exceção
para o valor de KPI em 24px — registrado para aplicação futura, já que este gate
não pode tocar o arquivo.

### Gate V2-2, Task 1/2 — granularidade selecionável e série do período anterior

**TECNICAMENTE CONCLUÍDA E VERSIONADA (07/08/2026), em publicação faseada.** A rodada
de implementação e a **única** rodada consolidada de correção do V2-2 foram
executadas; o orçamento de correção do gate está esgotado. Fecha as duas limitações
que o V2-1 declarou em texto no card de Evolução. É a **primeira alteração de
backend do ciclo de revamp**: `GET /api/v1/performance/trend` foi estendido de forma
**aditiva**, sem endpoint novo, tabela, read model, dependência ou pipeline.

O que o contrato passou a aceitar e devolver:

- **`granularity=auto|day|week|month`** (novo parâmetro, opcional). `auto` é o
  default e reproduz exatamente a regra anterior: **dia** até 92 dias de janela,
  **mês** acima disso. `week` é um grão novo, com semana **ISO-8601 começando na
  segunda** (`DATE_TRUNC('week', …)` do Postgres). Valor fora da allowlist devolve
  **422**, e a expressão SQL é escolhida por **mapeamento sobre a allowlist já
  validada** — a string do usuário nunca é interpolada no SQL.
- **`comparison`** (novo campo, opcional na resposta): quando o filtro global tem
  comparação ativa, vem `{date_from, date_to, data}` com a série do período
  anterior sob os **mesmos** canais, marcas e granularidade; quando não há
  comparação, vem `null`. O SQL da série tem **uma única implementação**,
  parametrizada pelo período — as duas janelas não podem divergir por construção.

Na interface, o card de Evolução ganhou um **seletor de grão de quatro estados**
(Automática / Diária / Semanal / Mensal). Em Automática, o card informa o grão que
foi **resolvido**, para o eixo nunca ficar sem explicação. A granularidade entra
**somente** na identidade das séries (`buildChannelSeriesKey` e o cache key de
`fetchTrend`), não na chave global: trocar o grão refaz apenas as chamadas de
`/trend` dos canais selecionados e não toca as outras cinco fontes; trocar a
métrica continua sem nenhuma requisição.

A série anterior é desenhada como **uma linha tracejada neutra** — o total do
período anterior, não seis séries concorrendo com a leitura principal —, e o
alinhamento com o período atual é por **posição ordinal**, preservando a data e o
rótulo reais dos dois lados no tooltip e no detalhe do ponto. Julho (31 buckets)
contra fevereiro (28) deixa os três últimos pontos **sem par**, e ausência de par
permanece ausência: nunca vira R$ 0. O total anterior só é exibido quando **todos**
os canais comparativos estão completos; comparação parcial, em erro ou sem
registros tem estado próprio e **não apaga a série atual**. Com `compare=false`,
nenhuma interface comparativa é renderizada.

O CTA do ponto continua fixando o **período atual**, agora cortado pelo intervalo
global: no grão semanal a primeira e a última semana são quase sempre parciais, e o
drill-down avisa quando o intervalo foi cortado em vez de aplicar datas que o
usuário não estava vendo.

**Compatibilidade retroativa:** clientes que não enviam `granularity` recebem o
mesmo comportamento de antes — compatibilidade retroativa de
comportamento, com schema aditivo (a resposta passa a incluir `comparison`, mesmo
que `null`, portanto o JSON não é idêntico). Uma API ainda sem o campo continua
respondendo, e o frontend lê essa ausência conforme a intenção do usuário: com
`compare=false` é "não solicitada"; com `compare=true` é **indisponibilidade
declarada**. Na prática isso significa que **a publicação do frontend que depende do
contrato novo exige um deploy manual da API no Render antes** — sem ele, o grão
explícito cai em estado de contrato incompatível e a comparação em indisponível
(degradação visível, não um gráfico errado).

**A entrega foi publicada em duas fases, nessa ordem.** Fase A: os 8 arquivos de
backend no commit **`e8f0630`**, publicado **manualmente no Render**. Em seguida, um
**smoke read-only PASS** contra a API publicada, somente `GET` e sem retry, confirmou
com **dados reais**: grão semanal com semana ISO (5 buckets, todos em segunda-feira);
janela comparativa canônica — junho/2026 comparando com **01–31/05**, não com a janela
deslizante; a **mesma** janela declarada por `/overview`; a soma da série atual
reconciliando com o `current.gmv` do `/overview` em **R$ 0,00** e pedidos idênticos;
período customizado de 10 dias comparando com 30/06–09/07; `granularity` inválida em
**422**; `compare=false` devolvendo `comparison: null` com o campo presente; e o modo
automático preservado (30 dias → grão diário). Os 27 paths publicados são idênticos
aos do código — nenhum endpoint desapareceu. Fase B: os 11 arquivos de frontend e
estes três documentos, versionados nesta entrega.

**A publicação do frontend segue o fluxo automático GitHub→Vercel** e para no push: o
deployment **não** é declarado Ready, **não** foi consultado e **não** passou por
smoke visual em produção.

**A rodada de correção fechou três findings materiais**, um deles bloqueador e de
origem anterior a este gate. (1) A **janela comparativa divergia dos KPIs**: havia
duas definições de "período anterior" no código — a janela deslizante de mesma
duração entregue pelos filtros e a correção de mês-calendário aplicada localmente em
`/overview`, `/brands` e `/quality`. Com junho selecionado, o KPI comparava com
01–31/05 e a nova série com 02–31/05; `/canais` e `/financeiro` já ecoavam a janela
deslizante antes deste gate, então o defeito não foi criado aqui — foi tornado
visível ao colocar as duas leituras no mesmo card. Agora existe **uma** função
canônica na camada de período (`resolve_compare_period`), aplicada nos dois
dependencies de filtros, e os **seis** endpoints agregados reportam o mesmo
intervalo, verificado tanto no valor ecoado quanto no `start`/`end` que chega ao SQL.
(2) As **datas reais da comparação eram descartadas**: o backend já as devolvia e o
frontend reconstruía o intervalo a partir do primeiro e do último bucket — errado em
semana parcial, em mês parcial e com janela vazia. As datas passaram a ser
transportadas do contrato até a interface, e janela desconhecida ou divergente entre
canais virou estado nomeado que bloqueia o total anterior sem apagar a série atual.
(3) **Granularidades diferentes não são mescladas**: a regra "a mais grossa vence"
não tornava as séries compatíveis, apenas escondia o problema; grãos distintos entre
canais, ou um grão explícito ignorado pela API, agora expõem estado próprio, sem
merge, sem conversão e sem reagregação, nomeando canais e grãos. O teste que
legitimava a regra antiga foi removido.

Validações: **485 testes de backend** (36 focais novos), **580 no frontend** (34
focais novos), typecheck e build verdes. Nenhum arquivo das outras dez rotas foi
alterado. *Estado registrado naquela rodada: a propagação visual às outras
superfícies (Task 2/2) e o V2-3 ainda não haviam sido iniciados — ambos foram
concluídos depois e versionados no fechamento de 10/08/2026.* Detalhe dos
contratos em §17 do [GERENCIAL_V2_SPEC.md](GERENCIAL_V2_SPEC.md), e do smoke da API
publicada em §17.9.

### Gate V2-2, Task 2/2 — propagação visual às dez superfícies

**APROVADA e versionada no fechamento do Revamp V2 (10/08/2026)**, junto com o V2-3 e o V2-4. A publicação segue o fluxo automático GitHub→Vercel e **ainda não foi validada**.

A **Task 1/2 está publicada e validada em produção como PASS WITH ISSUE**: backend
`e8f0630` no Render com smoke read-only PASS, frontend `2336567` no ar e confirmado
por assinatura funcional, com grão semanal, comparação canônica (junho/2026 →
01–31/05) e reconciliação trend × overview em **R$ 0,00**. O único issue é
**cosmético** — no mobile de 390px os cinco rótulos semanais do eixo X ficam colados
(folga mínima −1px) e o último aparece cortado — e está **reservado ao V2-3**, não
corrigido aqui.

Nesta task, as dez superfícies restantes (`/canais`, `/produtos`, `/regioes`,
`/financeiro`, `/qualidade`, `/pedidos`, `/tempo-real`, `/inteligencia`,
`/operacoes`, `/brand/[brand]`) receberam a linguagem visual do V2 **sem nenhuma
alteração de contrato de dados, métrica, filtro, endpoint ou regra de negócio**.
Dois componentes compartilhados foram extraídos com consumidores reais —
`layout/PageContainer` (10 rotas) e `layout/PageHeader` (9; `/brand` mantém
cabeçalho próprio) —, o container passou de `max-w-7xl` para `max-w-[1440px]` com o
ritmo `gap-3`/`gap-4` da Gerencial, a linha de escopo saiu de um ajuste `-mt-3` para
o cabeçalho, e a barra de filtros virou **sticky apenas nas cinco rotas que herdam
filtros globais**.

**Nenhum drill-down novo foi criado**, por decisão: fora de `/canais` nenhum bloco
reúne contexto, evidência, limitação e próximo passo com os dados já carregados, e
requisição nova estava proibida. O diálogo marca × canal de `/canais` foi preservado
intacto, com um único shell.

Todas as limitações de dado seguem visíveis e agora travadas por teste: N/D do
TikTok em Qualidade (nunca 0%), ausência de cobertura Shopee em Pedidos (nunca "API
offline"), ROAS por canal sem consolidação, cobertura regional distinta de GMV
total, e indisponibilidade explicada em Tempo Real / Inteligência / Operações.
O **checkpoint do GMV TikTok com frete não foi implementado**: produção segue em
`sub_total`, `KPI_META` não afirma frete e a escolha de coluna continua pendente.

Validações: **605 testes** (25 focais novos), typecheck e build verdes, detector do
Impeccable sem findings; `app/page.tsx` e `src/components/gerencial/**` intocados.
*Estado registrado naquela rodada: sem QA integrado e sem validação em produção — o
sanity visual cobriu render, overflow e erro fatal, e o V2-3 ainda não havia sido
iniciado.* O QA integrado foi executado depois, no V2-3, e o fechamento terminal no
V2-4. Detalhe em §14 do [UI_REVAMP_V2_PLAN.md](UI_REVAMP_V2_PLAN.md).

### Gate V2-3 — correção consolidada e QA integrado

**`BLOCKED — REPLAN REQUIRED` (10/08/2026).** As duas correções deste gate foram tecnicamente aprovadas, mas um finding material na própria Gerencial impediu o encerramento; o fechamento passou ao Gate V2-4, sem V2-3.1. Nada foi versionado.

A **Task 2/2 do V2-2 não foi aprovada isoladamente**: a revisão encontrou um finding
material — a barra de filtros anunciada como sticky estava **estruturalmente
inoperante**, porque `position: sticky` é limitado pela caixa do elemento pai e a
barra vivia dentro de um wrapper que terminava logo depois dela. Medido em `/canais`:
topo da barra em **−336px** no desktop e **−667px** no mobile após o scroll. O teste
que a cobria verificava apenas a presença da classe CSS, não o comportamento.

O trabalho foi **absorvido pelo V2-3**, sem criar V2-2.1 nem outro subgate, e
**consumiu a única correção consolidada do V2-3**.

Duas correções: (1) `PageHeader` passou a devolver um **Fragment**, de modo que
cabeçalho e barra são irmãos no fluxo do container da página — sticky provado por
medição em **`top=0px`** nas cinco rotas e nos três viewports; (2) os rótulos do eixo
X da Gerencial deixaram de colidir e de ser cortados no mobile de 390px
(`interval="preserveStartEnd"` + `minTickGap` + margem direita), com a fonte mantida
em 12px e sem tocar bucket, granularidade, comparação ou requisições.

**Finding material NÃO corrigido, registrado:** a barra sticky da **própria
Gerencial** (`GerencialHeader`, do V2-1) tem o mesmo defeito — topo em −729px
(desktop), −597px (tablet) e −631px (mobile). O gate autoriza tocar a Gerencial
apenas nos ticks, então a correção fica pendente de decisão própria.

QA integrado em 3 viewports × 11 rotas: render, overflow zero, sticky medido antes e
depois do scroll, ausência de barra nas rotas sem filtros globais, estado parcial com
falha induzida (fontes frescas permanecem, ausência nomeada, nenhum skeleton preso),
diálogo com shell único e foco devolvido, e as limitações de canal preservadas
(N/D do TikTok, cobertura Shopee, ROAS por canal, GMV regional × total). Validações:
**607 testes**, typecheck, build e detector do Impeccable verdes.

**Limite de ambiente declarado:** o build local não alcança dado real (API local
ausente; API pública fora do CORS para o origin local), então onde dado real era
necessário o harness interceptou as requisições e as repassou — técnica de teste, sem
alterar repositório, backend ou build. **Nada foi validado em produção.**

### Gate V2-4 — correção terminal do sticky da Gerencial

**`PASS` — APROVADO, e realizou o fechamento terminal do Revamp V2 (10/08/2026).** Versionado neste fechamento; a publicação automática ainda não foi validada. **Não houve V2-3.1 nem V2-4.1.**

**O V2-3 foi reclassificado como `BLOCKED — REPLAN REQUIRED`.** Suas duas correções
foram tecnicamente aprovadas, mas o QA encontrou um finding material na própria
Gerencial: `GerencialHeader` tinha o mesmo defeito estrutural de sticky que o
`PageHeader` — barra com topo em **−729px** (desktop), **−597px** (tablet) e
**−631px** (mobile) após o scroll. Um sticky inoperante na tela principal não é
ressalva cosmética, então o veredito anterior de "PASS WITH ISSUE" está revogado.
**Nenhum código havia sido versionado**, e o fechamento passou a este V2-4, sem
criar V2-3.1.

A correção foi de **um arquivo de produto**: `GerencialHeader` passou a devolver um
**Fragment**, tornando cabeçalho e barra irmãos no fluxo do container da Gerencial,
cuja caixa abrange toda a página. `app/page.tsx` não precisou mudar. Título,
subtítulo, período, `refreshedAt`, badge, estado "Atualizando dados…", filtros via
`children`, `useGlobalFilters`, querystring e request identity preservados; zero
fetch, estado, modal ou filtro novo; `EvolutionChart` intocado neste gate.

QA focal: a barra ancora em **`top=0px`** nos três viewports, com o título fora de
cena, conteúdo visível abaixo dela, 20 controles focáveis, diálogo acima da barra
(z-index 50 × 30), foco contido, Escape e devolução de foco ao gatilho, zero
overflow e zero erro fatal — e as mesmas seis fontes de dados. Regressão limpa:
`/canais` mantém `top=0px` em desktop e mobile, `/produtos` segue sem barra.

Não re-medido aqui: os **ticks semanais no mobile**, porque o gráfico exige série
real e o build local não a alcança (API local ausente, API pública fora do CORS, e o
proxy do harness passou a falhar nesta rodada). A evidência do V2-3 permanece
aplicável — `EvolutionChart` não foi alterado e um teste trava os quatro parâmetros
que produzem o comportamento.

Validações: **612 testes** (5 focais novos), typecheck, build e detector do
Impeccable verdes. Com o sticky da Gerencial aprovado nos três viewports, o
**Revamp V2 (V2-0 a V2-4) está tecnicamente concluído e APROVADO** — versionado
no fechamento de 10/08/2026, com a publicação automática ainda **não validada**. Dívidas pré-existentes mantidas: alvos de
toque dos filtros, `fetch` cru em `/brand`, dois `<h1>` do shell, e o checkpoint do
GMV TikTok com frete, ainda não implementado.

### Smoke pós-publicação do Revamp V2 — `PUBLICADO — PASS WITH ISSUE`

**Commit publicado: `04d0d17`. Smoke read-only executado em 10/08/2026** contra
`https://mktplace-gobeaute.vercel.app`, somente `GET` e navegação, sem qualquer
alteração em Vercel, Render, domínio, variável ou CORS.

Evidências principais: **11 rotas em HTTP 200** e backend (`/openapi.json`,
`/health-datasource`) em 200; **zero** fallback "Demonstração · API offline" nas
fontes saudáveis; **sticky da Gerencial em `top=0px` nos três viewports** (117px/13%
no desktop, 195px/25% no tablet, 239px/28% no mobile), contra −729/−597/−631px antes
da correção; **zero overflow horizontal**; ticks semanais legíveis, sem corte nem
colisão; comparação anterior com a janela canônica declarada e tooltip com data
completa; diálogo com shell único, foco contido, Escape e retorno de foco; Canais e
Produtos sem regressão. **Zero finding causado pelo release.** A correspondência ao
commit é comprovada **comportamentalmente** — não houve acesso ao painel autenticado
da Vercel.

**Restrição operacional aberta (não é regressão do Revamp):** as **quatro**
superfícies do Gate G4 — `/api/v1/performance/brand-detail`,
`/api/v1/performance/tempo-real`, `/api/v1/performance/inteligencia` e
`/api/v1/performance/operacoes` — respondem **500** porque dependem do **Data Mart,
inalcançável a partir do Render** (Gate G4). *A redação anterior desta seção citava
apenas três: `/brand-detail` também falha, e o smoke não o detectou por janela de
espera curta — corrigido no Gate S0.* No browser o erro aparece rotulado como CORS
apenas porque a resposta 500 não carrega o cabeçalho de origem; o CORS está correto.
As **quatro** páginas degradam honestamente, mas a Torre **permanece incompleta**
nessas superfícies. Corrigir CORS isoladamente não resolve: falta **fonte**. A
correção definitiva exige uma camada de serving acessível ao backend — a direção
recomendada é materializar/sincronizar esses dados no Neon por uma orquestração
server-side futura, possivelmente Airflow. **Nada dessa arquitetura foi
implementado.**

Dívidas não bloqueantes: um controle de filtro sem nome acessível, um abaixo do
tamanho mínimo de alvo, e a decisão do **GMV TikTok com frete** ainda pendente
(produção em `sub_total`). Detalhe em §17 do
[UI_REVAMP_V2_PLAN.md](UI_REVAMP_V2_PLAN.md).

### Gate S0, Task 1 concluída — blueprint aprovado (camada de serving Data Mart → Neon)

**CONCLUÍDA e APROVADA em 11/08/2026**, após uma rodada consolidada de correção.
Auditoria e blueprint, **read-only e documental**: **nenhuma DAG, schema, sync,
endpoint ou migração foi implementado**, e **o Gate S1 não foi iniciado**. Nada de
serving, Airflow ou endpoint foi corrigido — as quatro superfícies seguem
indisponíveis em produção. O **Revamp
Visual V2 permanece encerrado** (V2-0 a V2-4, publicado e validado).

**Correção factual sobre o smoke de 10/08/2026.** Aquele smoke reportou 500 em três
superfícies e tratou `/brand-detail` como saudável. **Estava errado, por limitação do
instrumento:** a passagem rápida esperava 2,2s por rota e o request de `brand-detail`
— disparado depois do `/daily` na página de Marca — não concluía nessa janela. Medido
agora com 9s e por `curl` direto: **as quatro superfícies do Gate G4 respondem 500**
(`/brand-detail`, `/tempo-real`, `/inteligencia`, `/operacoes`). O G4 estava certo.

Causa confirmada e inalterada: os quatro endpoints leem `gold.*` e são roteados ao
Data Mart (RDS), que **exige VPN** e é inalcançável do Render. O rótulo de CORS no
navegador é consequência de a resposta 500 não carregar `Access-Control-Allow-Origin`
— o CORS está correto, e ajustá-lo **não resolve**: falta fonte.

**Desenho recomendado:** copiar as fatos estáveis da gold para `marts.*` no grão da
origem (sem transformação, sem cache por endpoint), com sync por janela móvel em
transação única, staging temporária, validação por agregados, publicação atômica e
health check **por cobertura**, não por `MAX(data)`. **Quatro** tabelas novas mapeadas — não sete: `marts.fact_ml_produto_ranking` e
`marts.fact_tiktok_product_daily` **já existem**, são sincronizadas por
`pipelines/sync_produtos.py` e já servem a página de Produtos, e serão **reutilizadas**
(à segunda falta apenas uma migration aditiva de `active_videos` e `video_views` para
atender `/brand-detail`). Três fontes servem dois endpoints cada, o que é o argumento
contra cache por formato de resposta. O padrão já existe e está auditado neste repositório em
`pipelines/sync_region_daily.py`.

**Primeira fatia vertical recomendada: `/operacoes`** — três fontes diárias com
watermark natural, duas delas reaproveitadas por `/brand-detail`, sem view a
materializar e sem snapshot. `/tempo-real` fica deliberadamente **por último**: grão
horário, cadência intraday e um rótulo "ao vivo" que precisa de decisão de produto.

**Dois bloqueadores declarados:**

1. **O repositório Airflow existe** (informado pelo proprietário), mas **não foi
   localizado nem está visível** com as credenciais desta sessão — falta nome/URL,
   leitura para o token disponível (que enxerga só repositórios públicos de
   `Switerz` e `b2b-gogroup`, e não faz code search), e o modelo de hospedagem/rede.
   **Nenhuma DAG foi inspecionada, configurada ou executada.** Não bloqueia o
   blueprint; bloqueia a integração concreta.
2. **Não se presume que o worker alcance o Data Mart, e piloto não é prova.** O
   **piloto técnico** do módulo pode rodar de máquina com VPN e provar schema, carga,
   idempotência e reconciliação — mas **não** prova Airflow. A **prova operacional**
   exige `SELECT 1` no Data Mart, escrita no Neon e secrets resolvendo **de dentro do
   worker real**. Sem ela, o Gate S1 encerra como `PARTIAL — PILOTO VALIDADO`, o S2
   não ativa DAG nem agendamento, e nenhuma alegação de conectividade do Airflow pode
   ser feita.

Roadmap proposto em três gates — **S1 executado em 11/08/2026 como
`PARTIAL — PILOTO VALIDADO`** (registro abaixo); S2: `/operacoes` ponta a ponta com as duas fatos
TikTok novas; S3: `/brand-detail` e `/inteligencia`, **reutilizando** as fatos de
produto existentes e criando só `fact_ml_cross_company_summary`. **`/tempo-real` fica
para uma fase seguinte** (grão horário, cadência intraday e decisão de produto sobre o
rótulo "ao vivo"): ao fim dos três gates ele **continuará indisponível em produção**,
e isso não deve ser lido como camada de serving concluída. A decisão do **GMV TikTok com frete permanece frente separada** e não
entra nesta arquitetura: produção segue em `sub_total`. Blueprint completo em
[SERVING_AIRFLOW_PLAN.md](SERVING_AIRFLOW_PLAN.md).

### Gate S1 executado — `PARTIAL — PILOTO VALIDADO` (11/08/2026)

A primeira tabela da camada de serving está no Neon, carregada e reconciliada, **sem
troca de endpoint**. Migration `006` aplicada (`005 → 006`, tentativa única, exit 0),
criando `marts.fact_ml_gestao_diaria` — relações em `marts` de 31 para 32, nenhuma
existente alterada. Contrato conferido no banco: 9 colunas, `roas` nullable, PK
`(ref_date, brand)`, índice `(brand, ref_date)`, 5 CHECKs (4 com `<> 'NaN'`).

Duas publicações, ambas com `EXCEPT` bidirecional `(0, 0)`: backfill `s1t2-bf1` de
27/04/2025 a 10/08/2026 (0 apagadas, **1.621 publicadas**) e incremental `s1t2-inc1` de
04/08 a 10/08 (28 apagadas, 28 publicadas). Reconciliação independente do módulo fechou
em todos os campos nas duas janelas, checksum de negócio incluído. Destino final: 1.621
linhas, zero duplicidade de PK, zero nulo obrigatório, zero negativo, **zero linha de
11/08**, 902 `roas` NULL preservados.

**Por que `PARTIAL` e não `SUCCESS`.** O piloto técnico foi validado — migration, carga
histórica, incremental, isolamento da janela e reconciliação passaram. O resultado geral
fica em `PARTIAL` por **um único motivo**: a execução dentro de um **worker Airflow real
não foi comprovada**, e nenhuma infraestrutura Airflow, DAG, connection, secret ou pool
foi validada. `/operacoes` continuar no Data Mart **não é falha nem pendência do S1** — é
a fronteira esperada entre S1 e S2, onde a troca do endpoint está declarada.

Três pontos que não devem ser lidos como formalidade:

1. **Idempotência sob fonte estável: validação residual.** A segunda publicação
   controlada não ocorreu porque a fonte mudou entre o backfill e a revalidação — 2
   chaves, em 06/08 e 08/08, cada uma perdendo 1 pedido pago e R$ 75,90, por **maturação
   retroativa real** de status no ML. O incremental de sete dias corrigiu integralmente
   **−R$ 151,80** e **−2 pedidos**. Logo, não foi demonstrada em produção a idempotência
   dos **campos de negócio** sob fonte estável. O critério **não** é igualdade byte a byte
   da linha completa: `synced_at` e `source_run_id` são auditoria e mudam entre execuções
   por desenho. O piloto comprovou convergência, unicidade, reconciliação e isolamento da
   janela. **Isso não bloqueia o início do S2**; quando houver janela operacional estável,
   a contraprova deve comparar apenas chaves e campos de negócio, admitindo atualização
   dos campos de auditoria.
2. **O bloqueio anterior era real.** A primeira tentativa deste piloto parou como
   `BLOCKED` com a ingestão Shopee ativa (`raw.shopee_order_item_export` crescendo
   3,05 MB em 46 s). O critério que funciona é crescimento de relação por amostragem
   dupla, não silêncio de WAL: o Data Mart é réplica de leitura, e o processo de replay
   mantém `AccessExclusiveLock` rotineiramente.
3. **Limites conhecidos.** Nada do Airflow foi provado; o Render continua sem alcançar o
   Data Mart (o S2 resolve isso ao trocar a fonte do endpoint); e o `downgrade` da
   migration, escrito e restrito aos dois objetos do S1, nunca foi executado.

**Estado final: Gate S1 encerrado**, com a primeira tabela de serving disponível no Neon
(`marts.fact_ml_gestao_diaria`, 1.621 linhas até 10/08/2026, zero duplicidade, zero linha
do dia corrente, origem e destino reconciliados). **S2 Tasks 1/3 e 2/3 concluídas; Task 3/3 `BLOCKED`** (ver abaixo), porém
**tecnicamente desbloqueado** para desenhar e migrar `/operacoes` — o que **não** afirma
que o Airflow exista, esteja configurado ou tenha conectividade.

Registro completo em [SERVING_AIRFLOW_PLAN.md §26](SERVING_AIRFLOW_PLAN.md).

### Gate S2, Task 1/3 — implementada localmente, aguardando revisão (11/08/2026)

Contrato, schemas e sync das duas fatos TikTok de `/operacoes` prontos **localmente**.
**Nenhuma migration aplicada, nenhuma tabela TikTok criada no Neon** (que segue em
`alembic_version = 006`, com 32 relações em `marts`), **endpoint ainda em `gold.*`** e
**Airflow não comprovado**. Zero escrita em banco: todo acesso foi read-only.

Entregue: auditoria read-only das duas fontes; contrato congelado de `/operacoes` em 30
testes sem banco; migrations `007` e `008` escritas e não aplicadas (cadeia linear
`006 → 007 → 008`, head único); `pipelines/sync_tiktok_serving.py` com uma spec literal
por tabela; 73 testes focais do sync e das migrations. Suítes completas verdes: **1.856**
em `pipelines/tests` e **515** em `apps/api/tests`. Dry-runs read-only, já com a
allowlist oficial de cinco marcas, confirmaram **1.546** linhas na fonte de marca e
**184.252** na de criador, janela terminando em D−1.

Três achados da auditoria que valem registro:

1. **A cobertura histórica é de ~10 meses, não 13.** As fontes começam em 05/10 e
   07/10/2025, 87 e 89 dias depois do piso de referência. O piso vale quando a fonte o
   possui; o backfill leva todo o histórico disponível e o diagnóstico declara o déficit
   em cada execução.
2. **`gold.tiktok_brand_daily.total_live_minutes` tem 2 valores negativos**, um deles
   −29.545.461, em 03/04 e 06/05/2026, em marcas do escopo — a soma histórica da coluna é
   negativa. É defeito de dado na ingestão TikTok, **sem impacto no payload atual** de
   `/operacoes` porque o bloco `lives` usa janela de 30 dias. Consequência: a coluna fica
   sem CHECK de não-negatividade, porque o contrato da task é copiar exatamente; corrigir a
   origem é outra frente.
3. **14 colunas de demografia são 100% nulas** na fonte, então as médias ponderadas de
   `brand-detail` retornam sempre `NULL` hoje.
4. **As duas tabelas Gold não são carregadas em sincronia.** Em 12/08/2026,
   `tiktok_brand_daily` já tinha 11/08 e `tiktok_creator_daily` não. A validação de
   cobertura recusa a janela nesse caso, em vez de publicar um dia vazio — a Task 2/3
   precisa alinhar a janela ao menor `MAX(date)` das duas fontes.

**Duas correções da revisão, aplicadas.** *Minimização de dados:* o sync copia **somente
as cinco marcas oficiais**, reutilizando a allowlist de
`pipelines.connectors.tiktok.connector.BRANDS_IN_SCOPE` — sem criar lista nova, com filtro
**parametrizado** (`brand = ANY(%(brands)s)`) nas duas queries e reprovação de marca
externa como defesa em profundidade. As marcas extras não têm consumidor autorizado, e
`creator` é handle público potencialmente identificável. Efeito: 398 e 13.282 linhas
descartadas. *Reconciliação exata:* toda métrica reconciliada passou a usar
`decimal.Decimal`, sem `float` em nenhum caminho e sem arredondar antes de comparar — com
197.448 ocorrências de `12345.67891`, a implementação anterior em `float` divergia do total
exato em 0,00278.

**GMV TikTok com frete permanece frente separada e inalterado.** Produção segue em
`sub_total`. Misturar a mudança ao S2 inviabilizaria as duas provas do gate: a comparação
Gold × Marts passaria a confrontar regras diferentes, e a garantia de payload idêntico
perderia sentido justo quando o teste de contrato precisa ser imutável. Detalhe em
[SERVING_AIRFLOW_PLAN.md §27](SERVING_AIRFLOW_PLAN.md).

### Preflight da Task 2/3 bloqueado por VPN — `BLOCKED` (12/08/2026)

O preflight operacional da Task 2/3 parou como `BLOCKED` **exclusivamente porque a VPN
estava desconectada** (nenhum adaptador de tunel, nenhuma rota para a faixa privada do
Data Mart, nenhum cliente em execucao). **Nenhuma escrita ocorreu**: zero migration, zero
`--apply`, zero DDL/DML. O Neon segue em `alembic_version = 006`, as duas tabelas do S2
continuam ausentes e nenhuma autorizacao foi consumida.

O lado do Neon passou por inteiro — Alembic linear com head unico `008`, os seis objetos de
007/008 ausentes, zero vestigio de migration parcial, grants suficientes (incluindo TEMP,
que a staging exige), zero concorrencia e zero lock. Sem acesso a fonte nao ha
`common_date_to` nem fingerprint, e nenhum `GO` foi emitido com numeros da rodada anterior.
Um risco caiu de peso no caminho: `marts` ja tem `fact_tiktok_product_daily` com 208.451
linhas em 73 MB, entao o porte da `creator_daily` tem precedente no mesmo banco.

**Finding corrigido:** o timeout expos hostname e IP privado, e o sanitizador de erros
preservava topologia — redigia so `usuario:senha@`. Os dois modulos de serving (S1 e S2)
passaram a **classificar** falhas de conexao em cinco categorias fixas e nunca ecoar a
mensagem nativa, omitindo DSN, hostname, IPv4/IPv6, porta, usuario, senha e nome de
database. Mensagens seguras (constraint, divergencia de agregado, `statement timeout`)
continuam legiveis, e um horario ou numero de versao nao e' confundido com endereco.
Categorias identicas nos dois modulos, verificadas por teste cruzado.

**Isso nao desbloqueia a Task 2/3 sozinho.** O mesmo preflight read-only precisa ser
repetido integralmente quando a VPN voltar; so o `GO` dele autoriza aplicar 007/008.
Detalhe em [SERVING_AIRFLOW_PLAN.md §28](SERVING_AIRFLOW_PLAN.md).

### Gate S2, Task 2/3 — `SUCCESS`: serving TikTok publicado e reconciliado (13/08/2026)

As duas fatos TikTok estao no Neon, carregadas e reconciliadas, **sem troca de endpoint**.
Migrations `007` e `008` aplicadas numa unica tentativa (`006 -> 007 -> 008`, exit 0);
relacoes em `marts` de 32 para 34.

Corte comum calculado na execucao: **`common_date_to = 2026-08-11`** — D-1 era 12/08, a
`brand_daily` ja tinha 12/08 e a `creator_daily` nao, entao o corte recuou para o dia que
ambas cobrem. Publicado: **1.551** linhas em `fact_tiktok_brand_content_daily`
(2025-10-05..2026-08-11, 311 datas) e **185.035** em `fact_tiktok_creator_daily`
(2025-10-07..2026-08-11, 309 datas), `EXCEPT` bidirecional `(0,0)` nas duas, em 7 s e 50 s.
O risco de `statement_timeout=300s` nao se materializou.

Reconciliacao independente conferiu contagem, chaves, datas, cinco marcas, min/max,
duplicidade, nulos, NaN, cobertura e todas as somas em `Decimal`. Zero linha fora da janela,
zero marca fora da allowlist, auditoria completa.

**Um alarme que era de collation, nao de dado.** O fingerprint da `creator_daily` divergiu
entre as bases com todo o resto identico. Causa medida: Data Mart em `en_US.UTF-8` e Neon em
`C.UTF-8`, e `STRING_AGG(... ORDER BY texto)` depende de collation. Com `COLLATE "C"` nos
dois lados os hashes batem, e a comparacao de conjuntos em memoria fechou em 185.035 tuplas
identicas, zero exclusiva de cada lado. Fica a licao para a Task 3/3: comparacao entre
engines exige collation explicita ou comparacao de conjuntos.

**Isolamento provado:** das 34 relacoes de `marts`, so as 2 novas mudaram de contagem.
`fact_ml_gestao_diaria` identica antes e depois (1.621 linhas, checksum `fe3ca591…`), 15
tabelas Shopee inalteradas. `gold_service.py` intocado — `/operacoes` segue em `gold.*`. **A
decisao de frete no GMV TikTok permanece separada e inalterada.**

**Task 3/3 nao iniciada**, Airflow segue sem prova e a contraprova de idempotencia continua
pendente. Detalhe em [SERVING_AIRFLOW_PLAN.md §29](SERVING_AIRFLOW_PLAN.md).

### Gate S2, Task 3/3 — `BLOCKED` por deriva de valor na janela coberta (13/08/2026)

A troca de `/operacoes` para `marts.*` **nao foi feita**: zero linha de codigo alterada, zero
escrita, zero sync, zero migration. O endpoint segue em `gold.*`.

O bloqueio veio do criterio de paridade, e a investigacao mudou o entendimento do problema.
A comparacao executou **as cinco consultas reais do endpoint** nas duas fontes e reconstruiu
o payload com o mesmo codigo de pos-processamento. Dentro do corte comum (2026-08-10)
**todas as chaves sao comuns** nas tres tabelas — 1.621, 1.546 e 184.257, zero exclusiva de
qualquer lado —, mas **64, 23 e 6 chaves tem valor diferente**. Nao e' erro de mapeamento: e'
a Gold **reafirmando** numeros de dias ja fechados depois da copia.

A magnitude e' pequena (`gmv` do ML difere −R$ 5.631,15, ou −0,0147%; TikTok brand tem `gmv`
e `orders` identicos) mas **visivel no payload**: `orders_7d` de uma marca sai 1.653 contra
1.656, e a contagem de videos de um criador sai 166 contra 165. Numero exibido que muda com a
fonte e' material numa torre de controle, e o contrato congelado nao admite alteracao.

**O achado que ultrapassa a Task 3/3:** medindo a data mais antiga com valor alterado, a
`ml_gestao_diaria` reafirma ate **68 dias** para tras e a `tiktok_brand_daily` ate **27**. O
`DEFAULT_LOOKBACK_DAYS = 7` dos dois modulos foi dimensionado por hipotese, nao por medicao —
um incremental de 7 dias corrigiria a ponta e deixaria **deriva permanente** nas datas mais
antigas, invisivel a qualquer checagem por `MAX(date)`. Nada foi alterado: dimensionar janela
de convergencia e' decisao de arquitetura, nao ajuste de constante.

Para fechar a Task 3/3: decidir a estrategia de convergencia (lookback pelo horizonte medido,
refresh periodico ou reconciliacao que detecte deriva fora da janela) e depois recarregar as
tres tabelas com backfill completo, trocando o endpoint na mesma execucao em que a paridade
for medida. Detalhe em [SERVING_AIRFLOW_PLAN.md §30](SERVING_AIRFLOW_PLAN.md).

### Gate S2, Task 3/3 — correcao de convergencia implementada localmente (13/08/2026)

**`READY FOR REVIEW — AGUARDANDO BACKFILL COMPLETO`.** Politica temporal D-1 no fuso do
Brasil, lookback incremental de 90 dias e troca de `/operacoes` para `marts.*`
implementados e validados **localmente**. **Nada publicado**: zero commit, push, deploy,
escrita em banco ou sync.

**Causa do BLOCKED anterior:** a Gold reafirma valores de dias ja fechados ate **68 dias**
para tras (`ml_gestao_diaria`) e **27** (`tiktok_brand_daily`) — muito alem dos 7 dias de
lookback dimensionados por hipotese. Um incremental de 7 corrigiria a ponta e deixaria
deriva permanente no meio da serie, invisivel a qualquer checagem por `MAX(date)`.

**Politica adotada.** `/operacoes` vira visao de dias fechados: teto **inclusivo em D-1**
nas cinco consultas, com os limites inferiores **nominais** inalterados (7, 14 e 30
dias). O tamanho efetivo da janela mudou de proposito: sem teto, o dia corrente podia
elevar o intervalo para 8/15/31 datas; agora sao exatamente 7/14/30 dias fechados — o
painel deixa de exibir um numero que mudava sozinho ao longo do dia. O dia vem do
fuso **America/Sao_Paulo** via `zoneinfo`, sem dependencia nova — sem isso, entre 21h e 00h
no Brasil o servidor em UTC ja teria virado o dia e o painel mudaria sozinho no fim da
tarde. O lookback incremental passou a **90 dias** nos dois modulos, com piso contratual de
7 explicito (antes o piso estava acoplado ao default no modulo TikTok, e mudar o default
teria elevado o piso silenciosamente). **90 dias e' a rotina, nao garantia eterna:** fica
registrado como politica futura um **backfill historico periodico**, semanal quando houver
Airflow — nao implementado, sem DAG e sem agendamento.

**Troca cirurgica:** so `/operacoes`. Zero `gold.`/`raw.` nas cinco consultas,
`_uses_datamart` devolve `False` e um teste roda o endpoint com `datamart_engine = None`
para provar que nada tenta o Data Mart. `/brand-detail`, `/inteligencia` e `/tempo-real`
seguem na gold. Contrato congelado alterado **apenas** em fonte e teto: nenhuma expectativa
de payload mudou, e foi assim que ele provou seu proposito.

**Ainda nao ha paridade de producao:** as `marts.*` seguem com os dados defasados do §30. A
proxima etapa exige **autorizacao de escrita para backfill completo** das tres tabelas, com
paridade medida na mesma execucao; so entao o endpoint pode ser publicado. Airflow continua
sem prova e **a decisao de frete no GMV TikTok permanece separada**. Detalhe em
[SERVING_AIRFLOW_PLAN.md §31](SERVING_AIRFLOW_PLAN.md).

### Gate S2, Task 3/3 — backfill completo e `/operacoes` validado (13/08/2026)

**`SUCCESS — BACKFILL COMPLETO E /OPERACOES PRONTO PARA VERSIONAMENTO`.** As tres tabelas de
serving foram recarregadas integralmente ate **D-1 = 2026-08-12** (America/Sao_Paulo) e
reconciliadas; `/operacoes` produz payload **identico** ao da Gold. **Nada publicado**: zero
commit, push ou deploy.

| Tabela | `run_id` | Publicadas | `EXCEPT` |
| --- | --- | --- | --- |
| `fact_ml_gestao_diaria` | `s2t3-full-ml` | 1.629 | `(0,0)` |
| `fact_tiktok_brand_content_daily` | `s2t3-full-brand` | 1.556 | `(0,0)` |
| `fact_tiktok_creator_daily` | `s2t3-full-creator` | 185.697 | `(0,0)` |

As tres fontes cobriam D-1 sem buraco e ficaram estaveis entre duas amostras separadas por
35 s. A reconciliacao conferiu linhas, chaves, datas, marcas, min/max, duplicidades, nulos,
NaN e todas as somas em `Decimal`, com comparacao linha a linha **por conjunto** — imune a
collation, aplicando desde o inicio a licao do §29.5. Zero linha do dia corrente, zero marca
fora da allowlist, um unico `source_run_id` por tabela.

**Isolamento:** das 34 tabelas de `marts`, exatamente 3 mudaram — as autorizadas. As 7
tabelas Shopee ficaram identicas e o Alembic seguiu em `008`. (Correcao factual: sao 7
tabelas Shopee, nao 15; o numero anterior contava indices.)

**`/operacoes`:** payload de Gold e de Marts identicos campo a campo com o mesmo teto D-1
(`alertas=0, ml_velocity=4, creators=30, lives=5, tk_daily=70`), e prova runtime com
`datamart_engine = None` mostrando as cinco consultas passando pela Session do Neon, todas
em `marts.*`. O Gate G4 deixa de bloquear este endpoint assim que ele for publicado.

**Correcao de redacao:** o teto D-1 **e' mudanca comportamental**. Os limites inferiores
nominais foram preservados, mas antes o dia corrente podia render 8/15/31 datas; agora sao
exatamente 7/14/30 dias fechados.

**Falta versionar e publicar.** Airflow continua sem prova e o backfill historico periodico
segue como politica do futuro Airflow. **Frete no GMV TikTok permanece frente separada.**
Detalhe em [SERVING_AIRFLOW_PLAN.md §32](SERVING_AIRFLOW_PLAN.md).

### Gate S2 — FECHADO: `PASS COM RESTRICAO` (17/08/2026)

**`/operacoes` esta em producao lendo o Neon.** A revisao
`41eb1719a2730f545aaebd038c616bf0d0746ff7` foi publicada **manualmente pelo proprietario
no painel do Render** — nao pelo agente, que nunca teve acesso executavel ao servico.

O endpoint passou de **500 em ~10,4 s** para **200 em 0,50 s e 0,44 s**; a queda de
latencia e' a evidencia de que deixou de esperar o Data Mart. `health-datasource` reporta
`active_source=neon_marts` com `db_connected=true`. Payload deterministico nas duas
leituras e **identico ao snapshot reconciliado no Neon** (nove agregados, zero
divergencia): `alertas=0`, `ml_velocity=4`, `creators=30`, `lives=5`, `tk_daily=70`, com
**zero dado de 17/08**. Sem regressao em `overview`, `daily`, `trend`, `canais` e
`quality`, todos 200.

**Restricao:** o serving cobre ML e TikTok brand ate **16/08** e TikTok creator ate
**15/08**, porque a fonte Gold do creator para em 15/08. O dia 16/08 e' **ausencia da
fonte, nunca zero fabricado** — o bloco `creators` agrega seis dias reais em vez de sete.

**Delta pos-snapshot registrado:** apos a sincronizacao, a Gold reafirmou uma chave do ML
em 14/08 — GMV R$ 27.805,78 (Marts) contra R$ 27.750,78 (Gold) e 286 contra 285 pedidos,
delta de R$ 55,00 e 1 pedido, ou 0,0060% do GMV e 0,0085% dos pedidos na janela de sete
dias. Classificado como **alteracao legitima posterior ao snapshot** e sera absorvido pelo
proximo incremental. **Nao estabelece tolerancia permanente.** A igualdade verificada e'
relativa ao snapshot da execucao, porque a Gold e' fonte viva.

**O Gate G4 nao foi fechado:** apenas uma das quatro superficies saiu do 500.
`/inteligencia`, `/tempo-real` e `/brand-detail` seguem em **500 (~10,8 s)** por
dependencia conhecida do Data Mart, e sao escopo do **Gate S3, ainda nao iniciado**. O que
se tornou independente do Data Mart foi `/operacoes`, nao o sistema.

**Smoke visual nao executado** — sem driver de navegador, e nenhuma dependencia foi
instalada. A pagina HTTP respondeu 200, o que **nao substitui** QA visual. **Airflow
continua inexistente e nao comprovado.**

Registro completo em [SERVING_AIRFLOW_PLAN.md §35](SERVING_AIRFLOW_PLAN.md).

## Próximas prioridades

1. **Piloto autorizado da ponte de serving** (Checkpoint O1 Task 2/2, ja implementada e
   testada, **nunca executada**): rodar o wrapper em modo diagnostico, conferir
   `effective_date_to` contra os watermarks reais, depois uma execucao manual completa com
   VPN ativa. So' depois confiar no agendamento.
2. **Revisar o horario das 06:00**, a divida de maior impacto: e' quando a VPN tende a estar
   fora, e em agosto apenas 5 dos 17 dias tiveram execucao agendada. A tarefa roda com
   `LogonType=Interactive`, ou seja, so' com o usuario logado.
3. **Decidir a cadencia do backfill historico periodico.** O lookback de 90 dias nao cobre
   correcao anterior a essa janela.
4. **Somente depois: Gate S3** — migrar `/inteligencia`, `/tempo-real` e `/brand-detail`
   para o Neon, removendo a dependencia operacional dessas rotas do Data Mart.
5. **Obter nome/URL e o modelo de hospedagem do Airflow** (auto-hospedado ou gerenciado) e o acesso de leitura ao repositório — é a próxima ação do Gate S0. Depois disso, **provar de dentro do worker real** o acesso ao Data Mart e ao Neon. O **piloto técnico** do módulo de sync pode avançar antes disso, de máquina com VPN, mas **não** prova o Airflow. **S1 executado como `PARTIAL — PILOTO VALIDADO`; S2 FECHADO como `PASS COM RESTRIÇÃO` — ver a seção de fechamento acima.**
6. Integrar essa materialização ao **Airflow da organização** — que **existe** segundo o proprietário, mas **não foi localizado nem está visível** com as credenciais desta sessão, e cuja plataforma, URL, hospedagem e rede ainda não foram informadas; **nenhuma DAG foi inspecionada, configurada ou executada** —, eliminando a dependência síncrona Render → Data Mart/VPN.
7. Depois disso, tratar as dívidas menores de acessibilidade dos filtros (nome acessível e tamanho de alvo).
8. Manter a decisão do **GMV TikTok com frete** como frente separada.
9. Observar as próximas execuções diárias do `full_daily` agendado antes de considerar o horário 06:00 definitivamente estável.
10. Transferir a rotina manual Shopee e iniciar a configuração administrativa da API oficial.
11. Fazer discovery do Octaprice em paralelo, sem iniciar implementação prematura.

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
