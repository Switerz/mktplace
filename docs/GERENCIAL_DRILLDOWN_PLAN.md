# Gerencial drill-down-driven — Plano do ciclo (Gate G1)

**Gate:** G1 — Task 1 (verdade dos alertas + desenho), somente leitura, sem código.
**Baseline:** `main` @ `2b7389c` (revamp U0–U6 publicado, Checkpoint P1 GO COM RESTRIÇÃO).
**Orçamento:** 3 prompts (Task 1 desenho · Task 2 implementação · Task 3 QA). Sem G1.1/G1.2.
**Reprodução usada:** `channels=all`, `date_from=2026-07-01`, `date_to=2026-07-31`, `compare=true`
(endpoint público `GET /api/v1/performance/executive-summary`, agregado, read-only).

Fonte de verdade: o **código atual**. Documentos históricos são contexto.

> **Continuação (Gate G2):** a evolução transversal do padrão de drill-down (contrato UX comum aos três conteúdos + primeira expansão em Canais) está desenhada em [DRILLDOWN_ARCHITECTURE.md](DRILLDOWN_ARCHITECTURE.md). Este documento permanece como registro do ciclo G1 (Gerencial), já concluído.

---

## 1. Problema observado

O Resumo Executivo atual (`ExecutiveSummaryCard`, renderizado full-width **antes** dos KPIs em `apps/web/app/page.tsx:258`) foi rejeitado pelo usuário:

- **card grande antes dos KPIs**, empurrando o conteúdo primário para baixo;
- **"9 riscos"** com peso visual equivalente, misturando tipos diferentes;
- **quatro/cinco alertas repetidos de custo alto no TikTok**, um por marca;
- texto **contraditório**: "Custo/GMV de 0,0% acima do usual";
- **alertas comerciais, de frescor e de cobertura misturados** na mesma lista "Atenções";
- clicar num insight **navega imediatamente** para outra página (via `<Link>`), sem explicar o desvio antes.

Reprodução (07/2026): `health.status=critical`, `summary="GMV caiu 58.1%…; 9 risco(s)"`, sendo os 9 = **5 `high_cost` (todos TikTok) + 4 `stale_data`**, mais 3 `changes` e 1 `data_warning`.

## 2. Persona e decisão suportada

| Persona | Decisão que a Gerencial precisa suportar |
|---|---|
| Time comercial/gestão GoBeauté (usuário primário) | "GMV/pedidos estão dentro do esperado? Se não, **qual marca/canal** puxou o desvio, **quanto** e **por quê** — antes de eu navegar para investigar?" |

O fluxo-alvo (herdado do revamp): **resumo → desvio → clique → explicação → detalhe verificável**. Hoje o clique pula a explicação.

## 3. Auditoria da verdade dos alertas

Matriz de todos os tipos atualmente produzidos (backend `apps/api/app/services/executive_summary_service.py`, reusando sinais de `performance_service.py`). Categorias propostas (§7): **D**=Desempenho, **E**=Eficiência/Operação, **C**=Confiança no dado.

| Tipo | Cat | Pergunta de negócio | Fonte / grain | Cálculo | Comparador / guarda de amostra | Severidade | Destino atual | Veredito |
|---|---|---|---|---|---|---|---|---|
| `growth` | D | Que marca acelerou? | `get_brands` (marca × período, MoM) | `mom_pct>0`, top 3 | piso `total_gmv_prev ≥ 10.000`; precisa `mom_pct` não-nulo | info | `/canais?brands=` | **Verdadeiro** |
| `drop` | D | Que marca caiu? | idem | `mom_pct<0`, top 3; `≤ -30%`→critical | mesmo piso | warning/critical | `/canais?brands=` | **Verdadeiro** |
| `missing_data` | C | O período veio vazio? | `get_overview` (agregado) | `gmv==0 and orders==0` | — | critical | `/` | **Verdadeiro** (mas é dado/filtro, não risco comercial) |
| `high_cancel_rate` | E | Cancelamento fora do normal do canal? | `get_quality` (marca × canal) | `rate ≥ mediana_canal × 1,5` | mediana só com **≥2 marcas**; `median>0`; nunca cruza canais; TikTok fora (sem fonte) | warning | `/qualidade?brands=` | **Verdadeiro** (regra sólida) |
| `high_cost` | E | Custo de marketplace alto? | `get_canais` sinal `custo_alto` (marca × canal) | `marketplace_cost_pct ≥ cost_p75` (nearest-rank, ≥2 marcas) | **só relativo (top-quartil, `≥ p75`); dispara mesmo sem dispersão — ex.: `[0,0,0,0,0]` → `0 ≥ 0` para todas** | warning | `/canais?brands=` | **FALSO POSITIVO / texto incorreto** — ver §4 |
| `low_regional_coverage` | C | Cobertura de UF baixa/parcial? | `regioes_service.get_summary` | `coverage_level in (low, partial)` | estrutural (não é desvio comercial) | warning(low)/info(partial) | `/regioes` | **Verdadeiro como aviso de dado** (não deve contar como risco comercial) |
| `stale_data` (diário) | C | Marketplace parou de ingerir? | `MAX(date)` de `fact_marketplace_daily_performance` por mkt, **não filtrado pelo período** | `hoje - MAX(date) > 3d` | por marketplace (nunca `MAX()` combinado); `max_date is None`→não gera (coberto por `missing_data`) | warning | `/canais` | **Verdadeiro** (aviso de dado; correção de 2026-07-15 já evita o falso-positivo de mês fechado) |
| `stale_data` (regional) | C | Regional desatualizado? | `MAX(date)` de `fact_marketplace_region_daily` | `hoje - MAX(date) > 3d` | `None`→não gera (coberto por `not_applicable`) | warning | `/regioes` | **Verdadeiro** (aviso de dado) |
| `not_applicable` (data_warning) | C | TikTok sem cobertura regional | `regioes_summary.channels_sem_cobertura_regional` | estrutural | — | info | `/regioes` | **Verdadeiro** (nota estrutural, não risco) |
| `health.status` + contagem "N riscos" | — | Saúde geral | derivado | `_health_summary(gmv_mom_pct, len(risks))` | conta **todos** os riscos, inclusive frescor/cobertura | — | — | **Necessário corrigir** — mistura dado com negócio (decisão #8) |

**Limitações conhecidas (já documentadas no código):** custo do TikTok tem base ~5,5% diferente do GMV comercial (`_TIKTOK_COST_WARNING`); comissão ML indisponível no mart (`_ML_COST_MISSING_WARNING`); margem de Produtos fora de escopo desta fase; frescor regional usa mesmo limiar de 3d — o `full_daily` **já** executa `gold_regional_incremental` e `sync_region_if_needed`, então o alerta regional significa que `MAX(date)` passou do limite **apesar** do mecanismo recorrente (ausência de linhas novas na origem ou desatualização operacional), não ausência de sync.

**Resumo do veredito (contagem exata):**
- **8 tipos verdadeiros:** `growth`, `drop`, `missing_data`, `high_cancel_rate`, `low_regional_coverage`, `stale_data` diário, `stale_data` regional, `not_applicable`.
- **1 falso positivo no cenário auditado:** `high_cost` (ver §4).
- **1 derivação semanticamente defeituosa:** `health.status`/`health.summary` (contava `len(risks)` cru, misturando dado com negócio — corrigido nesta Task 2).

Somente-qualidade-de-dado (nunca risco comercial): `stale_data` (diário/regional), `low_regional_coverage`, `not_applicable`, `missing_data` (este último é exceção de disponibilidade — força saúde crítica, mas não conta como risco comercial).

## 4. Causa exata do "custo alto = 0,0%"

**Provado com dado real (07/2026):** os 5 `high_cost` têm `metric_value` **exatamente 0** (não é arredondamento), todos TikTok.

Cadeia causal (todas no `performance_service._build_channel_rows` + `_channel_row`, reusada por `executive_summary_service._build_risks`):

1. `marketplace_cost_pct = _pct(fees, gmv)`; para TikTok `fees = abs(total_fees)`. Em 07/2026 o `total_fees` do TikTok **soma 0** — porém `cost_available = _COST_APPLICABLE[tiktok] and fees_n > 0` é **True** (há linhas de settlement, `fees_n>0`, mas o valor somado é 0). Logo `marketplace_cost_pct = 0/gmv = 0` para as 5 marcas.
2. `cost_vals = [0,0,0,0,0]` → `cost_p75 = _percentile_nearest_rank([0,0,0,0,0],75) = 0`.
3. Sinal: `if row.marketplace_cost_pct (0) >= cost_p75 (0)` → **`0 >= 0` = True para TODAS as marcas** (distribuição degenerada, sem dispersão).
4. `_build_risks` emite **um `high_cost` por marca** → 5 itens quase idênticos.
5. `description = f"Custo/GMV de {marketplace_cost_pct:.1f}% acima do usual…"` → imprime **"0.0%"** e rotula o **próprio custo** como "acima do usual" (não é o excesso vs mediana; e o valor real é 0).

**Classificação:** **falso positivo** com **texto incorreto para uma regra parcialmente válida**. O sinal `custo_alto` é um flag **relativo** (top-quartil dentro do canal) legítimo para a matriz de Canais, mas: (a) top-quartil de um canal de custo ~0 não é risco de negócio; (b) `>=` sobre distribuição sem dispersão marca 100% das marcas; (c) não exige `current > mediana` nem `current > 0`, então dispara com custo zero; (d) o texto mostra o custo como se fosse o desvio.

**Guardrail correto (implementado na Task 2, só na camada executiva).** Não se cria `MIN_MATERIAL_COST_PCT` (não há threshold comercial aprovado) e **não** se usa `p75 > mediana` (com 5 marcas isso ocultaria um outlier legítimo como `[5,5,5,5,6]`). Um `high_cost` só é emitido quando **todos**:
- o sinal `custo_alto` veio de Canais;
- valor atual, mediana e p75 do canal **existem**;
- `current > 0`;
- `current > median`;
- `current >= p75`.

Isso elimina `[0,0,0,0,0]` (nenhum `>0`) e a distribuição plana positiva `[5,5,5,5,5]` (nenhum `> mediana`), preserva o outlier real `[5,5,5,5,6]` (só o 6) e não inventa threshold. A `description` passa a informar **custo atual, mediana do canal, referência p75 e a diferença em p.p.** — nunca "X% acima do usual" (onde X é o próprio custo).

Escopo é **somente a camada executiva** (a matriz/sinais de Canais ficam intocados). **Dívida separada (fora da Task 2):** `cost_available` tratar `fees_n>0` com soma 0 como "custo 0% real" é uma nuance de contrato do mart em Canais — não corrigir nesta frente.

## 5. Wireframe textual

Decisões #1–#4 aprovadas: remover o card full-width; nova ordem cabeçalho → filtros → 4 KPIs → (tendência + Pulso) → tabela por marca.

### Desktop (≥ lg)
```
┌ Topbar (shell) ───────────────────────────────────────────────┐
│ Cabeçalho da página + badge fresh/demo + período              │
│ Barra de filtros (canal · marca · período · comparar)         │
├───────────────────────────────────────────────────────────────┤
│ [ KPI GMV ] [ KPI Pedidos ] [ KPI Ticket ] [ KPI ROAS ]       │  ← 4 KPIs no topo, nunca empurrados
├──────────────────────────────────────────┬────────────────────┤
│ Tendência de GMV (col-span-2)            │ PULSO DO PERÍODO   │  ← Pulso = coluna lateral compacta
│                                           │ Saúde: <status>    │     (mesma grid lg:grid-cols-3 já
│                                           │ ─ Desempenho (n)   │      existente em page.tsx:307)
│                                           │  • até 3 insights  │
│                                           │ ─ Eficiência (n)   │
│                                           │  • agrupados       │
│                                           │ [ Ver todos ]      │
│                                           │ ─ Confiança dado ⓘ │  ← contagem separada, discreta
└──────────────────────────────────────────┴────────────────────┘
│ Tabela Performance por Marca … / alerta operacional            │
```
Composição final (sem toggle): grid analítica de **três colunas** no desktop — a **Tendência ocupa as duas colunas da esquerda** (spanning 2 linhas) e a **coluna direita empilha, nesta ordem: (1) Pulso do período, (2) Desempenho por canal**. No mobile a ordem é **KPIs → Pulso → Tendência → Desempenho por canal → tabela por marca**, obtida com ordenação responsiva (`order-*`)/grid explícita, sem toggle.

### Mobile (< lg)
```
Cabeçalho + filtros
[ KPI GMV ] [ KPI Pedidos ]
[ KPI Ticket ] [ KPI ROAS ]
PULSO DO PERÍODO (após os KPIs, antes das análises longas)
  Saúde: <status>
  Desempenho (n) · até 3 · [Ver todos]
  Eficiência (n) · agrupados
  Confiança no dado: n avisos ⓘ
Tendência de GMV
Desempenho por canal
Tabela por marca
```

## 6. Hierarquia final

1. Cabeçalho + filtros (inalterados).
2. **4 KPIs** (clicáveis, drill-down já existente — inalterado).
3. Área analítica: **Tendência** (destaque) + **Pulso do período** (lateral no desktop; após KPIs no mobile).
4. Desempenho por canal + **Tabela por Marca** + alerta operacional (inalterados).
5. `ExecutiveSummaryCard` full-width é **removido**; sua informação é recomposta, priorizada e categorizada no Pulso.

## 7. Categorias e regra de priorização (determinística, testável)

**Categorias (decisão #7), mapeadas por `type`:**
- **Desempenho:** `growth`, `drop`.
- **Eficiência e operação:** `high_cost`, `high_cancel_rate`.
- **Confiança no dado:** `stale_data`, `low_regional_coverage`, `missing_data`, `not_applicable`.

**Agrupamento (decisão #6) — chave por REGRA, nunca universal:**
- `growth`/`drop`: `(category, type, brand, marketplace)` — **separados por marca** (marcas distintas nunca colapsam);
- `high_cancel_rate`/`high_cost`: `(category, type, marketplace)` — agrupados por regra e canal;
- `stale_data`: `(category, type, source, marketplace)` — diário e regional (source diferente) nunca colapsam;
- `low_regional_coverage`/`missing_data`/`not_applicable`: por tipo/origem/mensagem (`(category, type, marketplace, href)`), sem misturar avisos semanticamente diferentes.

Cada grupo preserva severidade máxima, representante (maior magnitude, desempate lexicográfico), `count` e os membros para o drill-down. Ex.: 5 `high_cost` TikTok → **1** item "5 marcas com custo acima da referência no TikTok Shop" (título próprio, nunca o da marca representante).

**Seleção dos 3 (decisão #5):** os 3 exibidos saem **apenas** dos pools **Desempenho + Eficiência/Operação** (Confiança no dado nunca disputa as 3 vagas — decisão #8). Ordenação determinística (nunca compara magnitudes de unidades diferentes):
1. **severidade:** `critical(0) < warning(1) < info(2)`;
2. **prioridade fixa por tipo:** `drop < high_cancel_rate < high_cost < growth`;
3. **magnitude desc** — comparada **somente entre itens do mesmo tipo** (mesma unidade): `|delta_pct|` (growth/drop) ou `|delta_abs|` em p.p. (custo/cancelamento);
4. **desempate lexicográfico estável** pela chave do grupo.
Top 3 → visíveis; resto atrás de **"Ver todos"**.

**Separação status-negócio × status-dado (decisão #8):** Confiança no dado aparece como **indicador compacto separado** ("N avisos de confiança no dado", clicável), **nunca** somado à contagem de riscos comerciais. `health.status` (agora baseado só em GMV + riscos comerciais válidos) continua exibido; `health.summary` foi reescrito no backend para mencionar só a variação de GMV e a **quantidade de atenções comerciais/operacionais** (nunca `len(risks)` cru). A contagem de confiança no dado é derivada separadamente no frontend.

Regra 100% pura sobre a lista achatada de `changes`+`risks`+`data_warnings` → testável sem texto livre (novos testes em `apps/web/tests/`).

## 8. Contrato do drill-down do insight

Reutiliza o shell acessível **`KpiDrilldownDialog`** (decisão #10) — nenhum modal/drawer novo. Ao clicar num insight, **abre explicação primeiro** (decisão #9); navegar é **CTA secundário** dentro do diálogo, preservando filtros via `buildHref`/`mergeFilteredHref` (decisão #11).

Conteúdo mínimo do diálogo (um novo componente `InsightDrilldownContent`, análogo a `KpiDrilldownContent`):
- **O que aconteceu** — `title`;
- **valor atual** — `metric_value` formatado por tipo;
- **referência** — `reference_value` + `reference_kind` (mediana / p75 / threshold / período anterior);
- **diferença** — `delta_abs` / `delta_pct` quando aplicável;
- **marca · canal · período** — `brand`, `marketplace`, período (já no topo do payload);
- **por que essa severidade** — texto curto derivado de `severity` + regra;
- **confiança/limitação** — `confidence_note` (ex.: "5 marcas no canal"; caveat de base do TikTok);
- **itens agrupados** — membros do grupo (`brand`, `metric_value`), derivados no frontend pela chave de agrupamento;
- **CTA secundário** — link para a tela de origem (`href`), filtros preservados.

**Campos aditivos mínimos no contrato existente** (`ExecutiveRisk`/`ExecutiveChange` em `apps/api/app/schemas/executive_summary.py`; refletidos no tipo do `api-client.ts`). Todos `Optional`/nulos → **retrocompatíveis**, sem endpoint novo:

| Campo | Tipo | Uso |
|---|---|---|
| `category` | `"performance" \| "efficiency_ops" \| "data_confidence"` | categorização explícita (§7) — evita o frontend inferir por `type` |
| `reference_value` | `float?` | comparador contra o qual a métrica foi julgada |
| `reference_kind` | `"median" \| "p75" \| "threshold" \| "previous_period"?` | o que a referência significa |
| `delta_abs` | `float?` | diferença absoluta vs referência (quando aplicável) |
| `delta_pct` | `float?` | diferença percentual vs referência (quando aplicável) |
| `confidence_note` | `str?` | amostra/limitação (ex.: nº de marcas, caveat de base) |

Agrupamento **não** exige campo novo: `(category, type, marketplace)` já é derivável. `stale_data` já traz `source/last_date/threshold_days/staleness_days` (aditivos existentes) — aproveitados no drill-down de dado.

## 9. Mudanças mínimas previstas (frontend/backend)

**Backend (`apps/api`)** — extensão estreita, sem endpoint/rota nova:
- `schemas/executive_summary.py`: adicionar os 6 campos aditivos acima (Optional).
- `services/executive_summary_service.py`: preencher `category`/`reference_*`/`delta_*`/`confidence_note` em `_build_changes` e `_build_risks`; aplicar o **guardrail do `high_cost`** (§4: `current>0` + `current>median` + `current>=p75`, mediana/p75 existentes + texto correto), reusando `channel_medians`; saúde comercial só considera GMV + `high_cancel_rate`/`high_cost` (frescor/cobertura/`not_applicable` não mexem; `missing_data` força crítico sem contar como comercial); `health.summary` reescrito sem `len(risks)`.

**Frontend (`apps/web`)**:
- **remover** `ExecutiveSummaryCard` full-width de `app/page.tsx`;
- novo **`PulsoPeriodoPanel`** (categorizado, top-3 + "Ver todos", contagem de dado separada) na faixa lateral;
- novo **`InsightDrilldownContent`** dentro de `KpiDrilldownDialog`;
- nova lib pura **`src/lib/executive-pulse.ts`** (categorização + agrupamento + priorização determinística), estendendo/substituindo o uso de `executive-summary.ts`;
- estender o tipo `ExecutiveSummaryData`/insight em `src/lib/api-client.ts` com os campos aditivos;
- preservar estados fresh/loading/error/empty e a proteção contra dados antigos já existentes (decisão #13).

**Sem:** rota nova, pedido individual, escrita, exportação, paginação, dependência, gráfico/threshold sem fonte (decisões #12/#14).

## 10. Arquivos prováveis da Task 2

- `apps/api/app/schemas/executive_summary.py`
- `apps/api/app/services/executive_summary_service.py`
- `apps/api/tests/test_executive_summary_service.py`
- `apps/web/app/page.tsx`
- `apps/web/src/components/ExecutiveSummaryCard.tsx` (removido/substituído)
- `apps/web/src/components/PulsoPeriodoPanel.tsx` (novo)
- `apps/web/src/components/InsightDrilldownContent.tsx` (novo)
- `apps/web/src/lib/executive-pulse.ts` (novo)
- `apps/web/src/lib/executive-summary.ts` (ajuste)
- `apps/web/src/lib/api-client.ts` (tipos aditivos)
- `apps/web/tests/executive-summary.test.ts` + novo `apps/web/tests/executive-pulse.test.ts`

## 11. Critérios de aceite da Task 2

1. `high_cost` **não** emite mais o falso 0,0% (exige `current>0` + `current>median` + `current>=p75`); descrição mostra custo atual vs mediana/p75, sem "0,0% acima do usual".
2. Insights repetidos da mesma regra/canal **agrupados** (5 TikTok → 1 item com contagem).
3. 3 categorias separadas; frescor/cobertura/ausência **não** contam como risco comercial nem inflam a saúde.
4. No máximo 3 insights iniciais + "Ver todos".
5. KPIs nunca empurrados para baixo (Pulso lateral no desktop; após KPIs no mobile).
6. Clique abre **explicação primeiro**; navegação é CTA secundário com filtros preservados.
7. Reuso do `KpiDrilldownDialog`; sem novo modal/drawer; foco/Escape/backdrop/retorno de foco preservados.
8. Estados fresh/loading/error/empty e guarda de dado antigo preservados.
9. Contrato só estendido de forma aditiva; sem endpoint novo; sem dependência nova.
10. `npm test`/`typecheck`/`build` (web) e `pytest` (api) verdes; regra de priorização/agrupamento coberta por teste puro.

## 12. Roteiro de QA da Task 3

- Backend: `pytest apps/api/tests/test_executive_summary_service.py` — casos: custo 0/degenerado não vira `high_cost`; custo genuinamente alto ainda vira; `category`/`reference`/`delta`/`confidence_note` preenchidos; agrupamento coerente.
- Frontend: `npm test` (lib pura de priorização/agrupamento) + typecheck + build.
- QA visual em navegador (Playwright temporário isolado em `%TEMP%`, Torre em 3100, mesmo procedimento do U6): Gerencial desktop/tablet/mobile — KPIs no topo, Pulso lateral/depois, top-3 + "Ver todos", clique abre diálogo antes de navegar, CTA preserva filtros, contagem de dado separada, sem overflow, sem `#418`, estados de erro/loading/empty coerentes.
- Reprodução do cenário 07/2026: confirmar que o "0,0%" sumiu e que os 5 TikTok viram 1 item (ou nenhum, se o guardrail suprimir).

## 13. Achados classificados

- **Bloqueador (do aceite da nova Gerencial):** `high_cost` falso-positivo 0,0% (regra + texto). Corrigir na Task 2.
- **Necessário:** agrupamento de repetidos; separação das 3 categorias; contagem de saúde não misturar dado com negócio; drill-down explica-antes-de-navegar; campos aditivos do contrato.
- **Dívida (fora da Task 2):** `cost_available` tratar `fees_n>0` com soma 0 como custo 0% real (contrato do mart em Canais); alerta de frescor regional pode indicar ausência de linhas novas na origem apesar do `full_daily` recorrente (investigação operacional, não código); comissão ML sem competência mensal; margem de Produtos ausente.
- **Fora do escopo:** endpoint/rota nova; pedido individual; escrita/exportação/paginação; gráfico/threshold sem fonte; re-auditoria do `torre_b2b`; correção do 0,0% nesta Task 1 (só especificada).

---

**Task 1 concluída (design/auditoria).** **Task 2 implementada (03/08/2026).** **Task 3 concluída (04/08/2026) — Gate G1 encerrado.**

**Task 3 — correção consolidada + QA visual (04/08/2026).** 8 findings de revisão corrigidos:
1. **Formatação orientada ao campo** — `formatReferenceValue`/`formatDeltaAbs`/`formatDeltaPct`/`metricLabel` puros: em growth/drop o valor é % mas a referência (GMV anterior) e o delta_abs são em BRL; custo/cancelamento em % e p.p.; stale em dias; missing/not_applicable nunca fabricam "0". Testes por unidade.
2. **Texto verdadeiro quando custo == p75** — descrição usa "diferença vs p75: +N p.p." (inclui +0,0), nunca "acima do p75" quando o delta é 0. Teste com `[1,2,3,4,4]` (delta exato 0,0).
3. **missing_data ≠ diagnóstico "Crítico"** — o Pulso mostra badge neutro "Dados indisponíveis"; `metric_value` do missing_data virou `null` (sem "0" fabricado).
4. **Evidência de stale_data propagada** — `source`/`last_date`/`threshold_days`/`staleness_days` chegam ao drill-down (origem, última data, defasagem, limite).
5. **Severidade consolidada do grupo** — pior de TODOS os membros (não só o representante por magnitude). Teste com severidades mistas.
6. **CTA por membro em grupos multi-marca** — cada membro com `href` tem ação própria acessível ("Abrir <marca> na tela de origem"), filtros preservados por `buildHref`; grupo unitário mantém o CTA único; sem modal empilhado, sem linha inteira clicável.
7. **`health.summary` sem contagem contraditória** — comunica só variação de GMV / ausência de comparação / dados indisponíveis. As quantidades vivem no Pulso (que conhece o agrupamento final).
8. **A11y/acabamento** — heading semântico "Pulso do período" em caixa normal; alvos ~44×44px; gestão de foco na troca lista↔detalhe (nunca no body); único `KpiDrilldownDialog`.

**QA visual (Playwright temporário isolado em `%TEMP%`, Torre em 3100, executive-summary interceptado com payloads sintéticos):** cenário normal (GMV −12%, 3 sinais priorizados, grupo de 5 marcas de custo, avisos de dado separados, **sem custo 0,0% falso**, sem console/pageerror, sem overflow), missing_data (badge "Dados indisponíveis", nunca "Crítico"), falha só do resumo (KPIs/tendência preservados, Pulso "Indisponível"), diálogo individual + "Ver todos" (3 categorias) + CTA por membro preservando querystring, desktop 1440×900 / tablet 768×1024 / mobile 390×844 (Pulso após KPIs no mobile, sem overflow), teclado/Escape/foco (foco no "Voltar" ao abrir grupo, volta à lista, retorna ao acionador ao fechar).

**Validações:** 50 testes focais + **412 da suíte da API**; **395 testes do web**, typecheck e build verdes; detector Impeccable **sem findings**. Nenhuma dependência de produção alterada. **Veredito: PASS. Gate G1 concluído** — sem commit/push/deploy.
