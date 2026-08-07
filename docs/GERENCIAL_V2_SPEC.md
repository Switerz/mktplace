# Gerencial V2 — Especificação da tela flagship

**Gate de origem:** V2-0 (desenho) — encerrado e versionado no commit `c110e85`
**Gate de implementação:** V2-1 — **implementado, aguardando revisão** (07/08/2026). Registro em §14
**Data do desenho:** 06/08/2026 (com uma rodada de correção consolidada e uma correção factual)
**Documento irmão:** [UI_REVAMP_V2_PLAN.md](UI_REVAMP_V2_PLAN.md) (auditoria, matriz de padrões, contratos de dados, sistema visual, roadmap)

**Escopo de dados:** nada aqui cria endpoint. A especificação **reutiliza endpoints existentes** — `/overview`, `/brands`, `/trend`, `/canais`, `/executive-summary`, `/quality`, `/regioes/summary` — e **aumenta de forma controlada o número de chamadas**: até **3** chamadas de `/trend`, uma por canal selecionado (§4). Isso exige coordenação de frescor e de **falha parcial** entre respostas; nenhum bloco pode se declarar completo se uma fonte necessária falhar.

---

## 1. Definição

**Persona:** gestão comercial e liderança de marketplace. Quem abre a Torre uma vez por dia para saber se o mês está de pé, e uma vez por semana para decidir onde mexer.

**Decisão que a tela sustenta:** *onde alocar atenção esta semana* — qual canal, qual marca, com que evidência e com que confiança.

**Princípio:** narrativa vertical contínua, não mosaico. Cada dobra responde a uma pergunta e entrega o gancho da seguinte.

| # | Pergunta | Bloco |
|---|---|---|
| 1 | O que aconteceu? | Cabeçalho + faixa de confiança + faixa de 5 KPIs |
| 2 | Como evoluiu? | **Evolução temporal (bloco dominante)** |
| 3 | O que merece atenção agora? | Pulso do período + resumo de canais |
| 4 | Onde aconteceu? | Saúde do volume por canal + Matriz Marca × Canal |
| 5 | Por que aconteceu / quais evidências? | Movimentos + Concentração por marca + drill-down de cada bloco |
| 6 | Para onde ir? | Fila de atenção + CTAs para Canais/Marca/Produtos/Regiões |

**Blocos de primeiro nível: 8** (teto de 9).

**Decisão de produto encerrada — bloco dominante:** a **Evolução Temporal** é o bloco dominante, com **7 colunas** no desktop; **Pulso + Canais** ocupam **5 colunas**; a **Matriz Marca × Canal** vem na **dobra seguinte**. Justificativa: ordem cognitiva "quando aconteceu → onde aconteceu". Esta decisão não está mais em aberto.

---

## 2. Blocos 1 e 2 — Cabeçalho, faixa de confiança e KPIs

### 2.1 Cabeçalho executivo compacto

- **Identidade:** `h1` "Visão Gerencial" + subtítulo de uma linha.
- **Contexto:** período formatado, `Atualizado em …` (somente com dado fresco) e badge live/demo — **preserva o guard atual**: metadados da última requisição resolvida nunca aparecem ao lado de um período novo.
- **Controles:** canal, marca, período/comparação na **barra sticky**, compactados em chips com contagem.

### 2.2 Faixa de confiança no dado (elemento próprio, **não** é KPI)

Faixa **horizontal compacta entre o cabeçalho/filtros e a faixa de KPIs**, clicável, abrindo o drill-down de disponibilidade, defasagem e avisos.

**Semântica corrigida na rodada consolidada — ver §15.2 para o contrato completo.** A faixa afirma **disponibilidade de série** (derivada dos estados por canal das chamadas de `/trend`), não "cobertura" inferida de `gmv != null`: um canal com série de valores zero é *disponível*, um canal sem nenhuma linha é *sem registros*, e um canal cuja fonte falhou é *indisponível*. Defasagem, avisos e fontes nomeadas vêm do `/executive-summary` **separadamente** — se ele não responder, a disponibilidade das séries continua valendo e a faixa declara que defasagem e avisos não foram verificados.

Sem nenhuma série concluída, a faixa mostra "Verificando disponibilidade das séries…" e **não** exibe número.

### 2.3 Faixa de 5 KPIs (decisão encerrada)

`grid-cols-2 lg:grid-cols-5`, nesta ordem:

| # | KPI | Valor | Delta / referência | Regras |
|---|---|---|---|---|
| 1 | **GMV** | `/overview.gmv` | **Delta + "vs. período anterior: R$ X"** | Único com comparação garantida (`prev_gmv`, `gmv_mom_pct`) |
| 2 | **Pedidos** | `/overview.orders` | **"Comparação indisponível"** | Preserva a ressalva de compradores (soma diária ≠ único no período) |
| 3 | **Ticket Médio** | `/overview.avg_ticket` | **"Comparação indisponível"** | — |
| 4 | **Investimento em Ads** | `/overview.ad_spend` | **sem delta** | Declara cobertura **ML + Shopee**; **TikTok `N/D`** |
| 5 | **ROAS por canal** | `ml_roas`, `shopee_roas` | **sem delta** | ML e Shopee **separados**; TikTok `N/D`. **Proibido** total consolidado, soma ou média simples. Se só um canal compatível estiver selecionado, **destacar esse canal** |

**Regra de honestidade:** nenhum delta é calculado no cliente. Onde o contrato não traz referência, o card diz "Comparação indisponível" — nunca um número derivado.

Subvalor de decomposição (`TK · ML · SH`) e área clicável inteira abrindo a decomposição, em todos os cinco.

---

## 3. Bloco 3 — Evolução temporal (dominante, 7 colunas)

- **Métrica:** `GMV` \| `Pedidos` — `TrendPoint` já entrega ambos; hoje `orders` é buscado e descartado.
- **Granularidade:** **exibida, não controlada** no V2-1 — rótulo explícito ("Granularidade diária — definida pelo intervalo"). Seletor só quando o backend aceitar o parâmetro (V2-2). **Nenhum controle visual morto.**
- **Comparação com período anterior:** **indisponível na série durante o V2-1**, e **declarada em texto**: "Comparação ativa nos KPIs; série do período anterior indisponível neste gráfico". Isso resolve a contradição atual, em que `compare` é buscado e silenciosamente ignorado pelo gráfico. **Sem toggle morto.**
- **Séries por canal:** uma série por canal selecionado, com legenda; o total por bucket é a soma das séries (§4).
- **Interação:** clique em ponto/barra → drill-down do dia.
- **Eixo:** corrigir a perda de precisão atual (`toFixed(0)` transforma R$1,4M e R$1,6M em "R$1M" e "R$2M") → uma decimal em milhões, milhar sem decimal.
- **Altura:** `flex-1 min-h-[280px]`, `ResponsiveContainer height="100%"`, card `h-full flex flex-col`.

---

## 4. Contrato da tendência por canal

`/trend` devolve **uma série agregada por requisição, sem dimensão de canal no payload**. Mecânica do V2-1:

1. **No máximo uma chamada de `/trend` por canal selecionado** — 1 canal → 1 chamada; 2 → 2; 3 → 3. **Até 3 chamadas.**
2. Cada chamada **reutiliza o endpoint existente** com **seleção unitária** de canal.
3. **Nenhum endpoint novo.**
4. **Nenhuma quarta chamada agregada:** o total por bucket é a **soma das séries numéricas** dos canais.
5. A soma por bucket **reconcilia** com o GMV/Pedidos agregado no mesmo escopo (o contrato de `/trend` já garante que a soma da série bate com `/overview` para a mesma cláusula de filtro). Divergência é **erro de bloco**, não arredondamento a ser escondido.
6. **Canal sem dado permanece distinguível de canal com zero real:** ausência é `N/D`; zero é `0`.
7. **Identidade da requisição** inclui: **canais, marcas, período, métrica e retry**.
8. **Resposta obsoleta nunca aparece** após a troca de filtro — `resolvedKey ≠ requestKey` ⇒ o bloco não renderiza valor.
9. **Falha parcial:** as séries disponíveis permanecem; o canal ausente é **nomeado**; o total **não** é apresentado como completo; **nenhum fallback silencioso**.
10. Métricas disponíveis: **GMV e Pedidos**.
11. Comparação com período anterior: **indisponível no V2-1**, declarada em texto.

---

## 5. Bloco 4 — Pulso do período + resumo de canais (5 colunas)

**Um único item de grade** `flex flex-col gap-4` contendo Pulso e resumo de canais — elimina o `row-span-2` que causa a lacuna.

Mantém o comportamento do G1/G2 (síntese, no máximo 3 insights priorizados, contagem **separada** de avisos de confiança, clique abre explicação antes de navegar) e ganha:

- **`max-h` com scroll interno** na lista de insights, para não alongar a coluna além do gráfico.
- **Elo com a série:** ao focar um insight que tenha `last_date`, o ponto correspondente do gráfico é destacado. Sem `last_date`, sem destaque — nenhuma inferência.

**Separação obrigatória (G1):** risco comercial e aviso de confiança no dado nunca na mesma lista. Ausência de dado não é diagnóstico comercial "Crítico".

---

## 6. Bloco 5 — Saúde do volume por canal

Substitui o funil monetário, **removido** por falta de lastro. O contrato **não** oferece valor monetário cancelado, valor monetário devolvido, GMV anterior às exclusões, nem etapas monetárias mutuamente exclusivas e comparáveis.

**Desenho:** **linhas independentes por canal** (small multiples). Cada métrica mantém **sua unidade e sua definição**; nenhuma métrica é representada como parcela exclusiva de outra.

| Canal | Métricas exibidas |
|---|---|
| **Mercado Livre** | GMV · **Pedidos considerados** · Cancelados (n) · Taxa de cancelamento · **Devolução `N/D`** |
| **Shopee** | GMV (shop-stats) · **Pedidos considerados** · Cancelados (n) · Taxa de cancelamento · Devolvidos (n) · **Taxa de devolução (métrica independente)** |
| **TikTok Shop** | GMV · **Pedidos registrados** · **Cancelamento `N/D`** · **Devolução `N/D`** — texto "Não disponível nesta fonte", **nunca zero** |

**Rótulos, com precisão:** `ml_total_orders` é o **total considerado** (`ml_orders + ml_canceled`), **não** "pedidos elegíveis". Em Shopee, `shopee_orders` é a população de **não cancelados** e o total considerado é `shopee_orders + shopee_canceled_orders`. Exibir **total considerado + cancelados + taxa**; "não cancelados" só é derivado (`ml_total_orders − ml_cancelled_orders`) se necessário. No TikTok, o rótulo é **"Pedidos registrados"** e **não se infere** total considerado incluindo cancelados.

**Regras invioláveis:**

1. **Proibida** qualquer barra segmentada que sugira parcelas monetárias exclusivas.
2. Cancelados e devolvidos são **contagens**, exibidas como contagem — nunca como fatia de um valor monetário.
3. A taxa exibida é **a servida pelo contrato**; o frontend **não recalcula** e **nunca usa outro denominador**. ML e Shopee compartilham **a mesma** definição: `cancelados / (não cancelados + cancelados)` (`sh_canceled / (sh_orders + sh_canceled)` em `performance_service.py:918`; em `:1341`, `sh_canceled / sh_total` com `sh_total = sh_orders + sh_canceled` em `:1336` — expressões idênticas; e `ml_total = ml_orders + ml_canceled` em `:455`, `:578`, `:1307`, `:1479`, `:1531`).
4. **Nenhum ranking competitivo de cancelamento entre canais.** A razão **não** é aritmética — a fórmula é a mesma. É que **fonte, processo de captura e semântica operacional dos status diferem entre marketplaces** (ML via API; Shopee via export manual com shop-stats como GMV autoritativo; TikTok com allowlist de status e maturação de 2–3 dias) e o **TikTok não tem cobertura confiável**. A Gerencial apresenta cada canal **descritivamente**; a comparação válida é do canal contra si mesmo, ou contra a mediana/p75 **do próprio canal**.
5. O drill-down **declara a definição** da taxa em texto.
5.1 **Devolvidos e taxa de devolução da Shopee são métricas independentes** — nunca apresentadas como partição exclusiva do total considerado.
6. O campo do TikTok existir no schema **não** torna o valor verdadeiro — é estruturalmente zero (DQ1) e vai para `N/D` com motivo.
7. Se nenhum canal selecionado tiver cancelamento confiável, o bloco **não** renderiza (em vez de renderizar vazio).

Fontes: `/overview` (GMV) + `/quality`:

| Campo | Significado | Uso no bloco |
|---|---|---|
| `ml_total_orders` | `ml_orders + ml_canceled` | **Pedidos considerados** (ML) |
| `ml_cancelled_orders` | cancelados | Cancelados (ML) |
| `ml_cancel_rate_pct` | `cancelados / total considerado` | Taxa (ML) — exibida como servida |
| `shopee_orders` | não cancelados na população servida | base do total considerado (Shopee) |
| `shopee_canceled_orders` | cancelados | Cancelados (Shopee); **Pedidos considerados** = `shopee_orders + shopee_canceled_orders` |
| `shopee_cancel_rate_pct` | `cancelados / total considerado` | Taxa (Shopee) — exibida como servida |
| `shopee_returned_orders`, `shopee_return_rate_pct` | devolvidos e taxa | métricas **independentes**, nunca partição do total |
| `tiktok_orders` | pedidos registrados | **Pedidos registrados** (TikTok) |

---

## 7. Bloco 6 — Matriz Marca × Canal (dobra seguinte)

Grade de marcas (linhas) × canais (colunas), células com `min-h-[56px]`.

Cada célula: GMV · share da linha · variação vs. anterior · chip de sinal quando houver. Intensidade de cor por share **dentro do canal** (nunca entre canais). Cabeçalho de coluna com total do canal; cabeçalho de linha com total da marca e link para `/brand/[brand]`.

Célula sem dado = cinza com "—" e motivo acessível + no drill-down. Clique na célula → drill-down marca × canal, reusando o conteúdo comparativo já existente em Canais (diagnóstico, mediana e p75 **do próprio canal**, sinais com "por quê").

---

## 8. Bloco 7 — Movimentos e concentração por marca

Três colunas iguais, alturas coordenadas por `max-h` + scroll interno:

1. **Maiores altas** — marca × canal, por variação absoluta
2. **Maiores quedas** — idem
3. **Concentração por marca** — `/brands` no **mesmo período global**: marca · GMV · participação no total selecionado · barra de share · concentração **Top 1** e **Top 3** quando houver base suficiente

**Por que não há ranking de produtos:** produtos não têm escopo temporal uniforme (ML é ranking acumulado atual; TikTok e Shopee são competência mensal). Um ranking de produtos sob o período global compararia três janelas diferentes sob o mesmo rótulo. Fica registrado como **evolução futura dependente de contrato temporal uniforme**, fora do V2-1.

**CTA para `/produtos`** permanece, com texto explícito de que **aquela tela tem contratos próprios por canal** — o clique não promete o mesmo período.

**Regra antifalsa dos movimentos:** variação percentual sobre base pequena engana. Cada item mostra **absoluto e percentual**, a ordenação usa o absoluto, e o piso mínimo é declarado no cabeçalho ("variações sobre base < R$ X mil ficam fora desta lista").

**Regra da concentração:** sem base suficiente, Top 1/Top 3 **não** é exibido — não se substitui por aproximação.

---

## 9. Bloco 8 — Fila de atenção

Fecha a narrativa e substitui o alerta hard-coded atual (`if (brand === "lescent")` no JSX).

Alimentado por `/executive-summary`, que **já entrega tudo**: `severity`, `category`, `title`, `description`, `metric_value`, `reference_value`, `reference_kind`, `delta_abs`, `delta_pct`, `confidence_note`, `source`, `last_date`, `staleness_days`, `href`.

Cada linha: severidade · alerta · impacto (métrica × referência **nomeada**) · evidência (fonte + data) · confiança (`confidence_note`) · ação (CTA com filtros preservados) · destino.

Duas listas **separadas**: risco comercial e avisos de confiança no dado.

---

## 10. Wireframes

### 10.1 Desktop 1440×900 (12 colunas, `max-w-[1440px] px-6 gap-4`)

```
┌──────────────────────────────────────────────────────────────────────────────────────┐
│ SIDEBAR │  TOPBAR (sticky)                                                           │
│ clara/  │ ┌────────────────────────────────────────────────────────────────────────┐ │
│ lavanda │ │ Visão Gerencial                          [● API]  Atualizado 14:02      │ │
│ 240px   │ │ Como estamos, onde investigar e para onde ir.  Período: 01–31/07/2026   │ │
│         │ ├────────────────────────────────────────────────────────────────────────┤ │
│         │ │ FILTROS (sticky, backdrop-blur)  [Canais 3▾][Marcas 5▾][Jul ▾][⇄ Comp] │ │
│         │ └────────────────────────────────────────────────────────────────────────┘ │
│         │ ┌────────────────────────────────────────────────────────────────────────┐ │
│         │ │ CONFIANÇA NO DADO  3 canais com cobertura · defasagem 1d · 2 avisos  ▸ │ │  ← faixa
│         │ └────────────────────────────────────────────────────────────────────────┘ │    própria,
│         │                                                                            │    NÃO é KPI
│         │  ── DOBRA 1: O QUE ACONTECEU ──                          5 cols × 1fr      │
│         │ ┌─────────┬─────────┬─────────┬─────────┬─────────┐                       │
│         │ │ GMV     │ PEDIDOS │ TICKET  │ INVEST. │ ROAS    │  alturas iguais        │
│         │ │         │         │  MÉDIO  │ EM ADS  │POR CANAL│                       │
│         │ │R$ 7,3M  │ 124.790 │  R$ 58  │ R$ 377k │ ML 4,2x │                       │
│         │ │▲ 8,4%   │Comparaç.│Comparaç.│(sem     │ SH 3,1x │  ← ROAS separado por  │
│         │ │vs R$6,7M│indispon.│indispon.│ delta)  │ TK  N/D │    canal, SEM total   │
│         │ │TK·ML·SH │         │         │ ML + SH │         │    consolidado        │
│         │ │         │         │         │ TK N/D  │         │                       │
│         │ └─────────┴─────────┴─────────┴─────────┴─────────┘                       │
│         │   ↑ delta SÓ no GMV                                                        │
│         │                                                                            │
│         │  ── DOBRA 2: COMO EVOLUIU / O QUE ATENDER ──       7 cols  │  5 cols       │
│         │ ┌────────────────────────────────────────────────┬────────────────────────┐│
│         │ │ EVOLUÇÃO (dominante)   [GMV | Pedidos]         │ PULSO         Atenção  ││
│         │ │ Granularidade diária — definida pelo intervalo │ ┌────────────────────┐ ││
│         │ │ ┌────────────────────────────────────────────┐ │ │ ⚠ Custo alto Shopee│ ││
│         │ │ │ R$1,4M ┤        ╭─╮                       │ │ │   Kokeshi · 29,4%  │ ││
│         │ │ │        │   ╭────╯ ╰──╮   ── TikTok        │ │ ├────────────────────┤ ││
│         │ │ │ R$0,7M ┤╭──╯         ╰─╮ ── ML            │ │ │ ⚠ Ads subutilizado │ ││
│         │ │ │        ││              ╰ ── Shopee        │ │ ├────────────────────┤ ││
│         │ │ │      0 └┴01──05──10──15──20──25──31       │ │ │ ℹ ROAS forte ML    │ ││
│         │ │ │        flex-1  min-h-[280px]              │ │ │ Ver todos (7) ▸    │ ││
│         │ │ └────────────────────────────────────────────┘ │ │ 2 avisos de        │ ││
│         │ │ 1 série por canal · até 3 chamadas /trend      │ │ confiança ▸        │ ││
│         │ │ Total por bucket = soma das séries             │ ├────────────────────┤ ││
│         │ │ ⓘ Comparação ativa nos KPIs; série do período  │ │ CANAIS (share GMV) │ ││
│         │ │   anterior indisponível neste gráfico          │ │ TikTok ███████ 41% │ ││
│         │ │ clique no ponto → drill do dia                 │ │ ML     █████   32% │ ││
│         │ │                                                │ │ Shopee ████    27% │ ││
│         │ └────────────────────────────────────────────────┴────────────────────────┘│
│         │   ↑ card É o item de grade, h-full flex flex-col   ↑ UM item de grade:      │
│         │     área do gráfico flex-1                          flex flex-col (sem      │
│         │   FINAIS ALINHADOS: diferença ≤ 24px                row-span)               │
│         │                                                                            │
│         │  ── DOBRA 3: ONDE ACONTECEU ──                            12 cols          │
│         │ ┌────────────────────────────────────────────────────────────────────────┐ │
│         │ │ SAÚDE DO VOLUME POR CANAL          métricas independentes, por canal    │ │
│         │ │                                                                        │ │
│         │ │ Mercado Livre    GMV R$1,78M │ Pedidos considerados 32.892           │ │
│         │ │                  Cancelados 2.480 · taxa 7,5% ███░░░░░░░               │ │
│         │ │                  Devolução N/D — não disponível nesta fonte            │ │
│         │ │ ────────────────────────────────────────────────────────────────────── │ │
│         │ │ Shopee           GMV R$1,86M │ Pedidos considerados 30.747           │ │
│         │ │                  Cancelados 1.842 · taxa 6,0% ██░░░░░░░░               │ │
│         │ │                  Devolvidos 412 · taxa 1,4% (métrica independente)     │ │
│         │ │ ────────────────────────────────────────────────────────────────────── │ │
│         │ │ TikTok Shop      GMV R$3,00M │ Pedidos registrados 65.473            │ │
│         │ │                  Cancelamento N/D · Devolução N/D                      │ │
│         │ │                  Não disponível nesta fonte                            │ │
│         │ │                                                                        │ │
│         │ │ ⓘ Taxa = cancelados / (não cancelados + cancelados), mesma definição   │ │
│         │ │   em ML e Shopee. Sem ranking entre canais: fonte, captura e semântica │ │
│         │ │   de status diferem por marketplace. Detalhe no drill-down.            │ │
│         │ └────────────────────────────────────────────────────────────────────────┘ │
│         │   ↑ contagens são contagens; barra ilustra a TAXA do próprio canal,        │
│         │     nunca uma parcela de valor monetário                                   │
│         │ ┌────────────────────────────────────────────────────────────────────────┐ │
│         │ │ MATRIZ MARCA × CANAL          [GMV ▾]        célula: min-h-[56px]       │ │
│         │ │            │  TikTok   │    ML     │  Shopee   │  TOTAL                 │ │
│         │ │ Barbours   │ R$820k ▲6 │ R$610k ▼2 │ R$540k ▲9 │ R$1,97M  →             │ │
│         │ │ Kokeshi    │ R$700k ▲4 │ R$520k ▲1 │ R$480k ⚠  │ R$1,70M  →             │ │
│         │ │ Ápice      │ R$540k ▼3 │ R$390k ▲7 │ R$350k ▲2 │ R$1,28M  →             │ │
│         │ │ Lescent    │ R$410k ▲2 │    —   ⚠  │ R$280k ▲4 │ R$690k   →             │ │
│         │ │ Rituária   │ R$330k ▲1 │ R$260k ▲3 │ R$210k ▼1 │ R$800k   →             │ │
│         │ │ TOTAL      │  R$3,00M  │  R$1,78M  │  R$1,86M  │ R$6,64M                │ │
│         │ │ clique na célula → explicação marca × canal (mediana/p75 do canal)      │ │
│         │ └────────────────────────────────────────────────────────────────────────┘ │
│         │                                                                            │
│         │  ── DOBRA 4: POR QUÊ ──                        4 cols │ 4 cols │ 4 cols    │
│         │ ┌───────────────────┬───────────────────┬───────────────────┐              │
│         │ │ MAIORES ALTAS     │ MAIORES QUEDAS    │ CONCENTRAÇÃO      │              │
│         │ │                   │                   │ POR MARCA         │ max-h +      │
│         │ │ Barbours·SH       │ Ápice·TK          │ Barbours R$1,97M  │ scroll       │
│         │ │  +R$44k  ▲ 8,9%   │  −R$31k  ▼ 5,4%   │  29,7% ██████     │ interno      │
│         │ │ Kokeshi·TK        │ Rituária·SH       │ Kokeshi  R$1,70M  │              │
│         │ │  +R$28k  ▲ 4,1%   │  −R$12k  ▼ 1,8%   │  25,6% █████      │              │
│         │ │ Ápice·ML          │ Barbours·ML       │ Ápice    R$1,28M  │              │
│         │ │  +R$19k  ▲ 5,1%   │  −R$ 9k  ▼ 1,5%   │  19,3% ████       │              │
│         │ │                   │                   │ ───────────────── │              │
│         │ │ ⓘ base < R$50k    │ ⓘ mesmo piso      │ Top 1: 29,7%      │              │
│         │ │   fora da lista   │                   │ Top 3: 74,6%      │              │
│         │ │                   │                   │ [Ver Produtos →]  │              │
│         │ │                   │                   │ ⓘ Produtos usa    │              │
│         │ │                   │                   │  contratos por    │              │
│         │ │                   │                   │  canal, não este  │              │
│         │ │                   │                   │  período          │              │
│         │ └───────────────────┴───────────────────┴───────────────────┘              │
│         │                                                                            │
│         │  ── DOBRA 5: PARA ONDE IR ──                              12 cols          │
│         │ ┌────────────────────────────────────────────────────────────────────────┐ │
│         │ │ FILA DE ATENÇÃO                              risco comercial (5)        │ │
│         │ │ ⚠ ALTO │Custo alto Shopee — Kokeshi                                     │ │
│         │ │        │29,4% vs mediana 21,1% e p75 27,8% do próprio canal             │ │
│         │ │        │evidência: marts · até 04/08 · confiança: sem CMV               │ │
│         │ │        │                              [Ver evidência em Canais →]       │ │
│         │ │ ⚠ MÉD  │Ads subutilizado ML — Ápice        [Abrir marca →]               │ │
│         │ ├────────────────────────────────────────────────────────────────────────┤ │
│         │ │ AVISOS DE CONFIANÇA NO DADO (2)  — lista separada, nunca misturada      │ │
│         │ │ ℹ Cancelamento indisponível no TikTok Shop     [Entender →]              │ │
│         │ │ ℹ Cobertura regional parcial (43,8% do GMV)    [Ver Regiões →]           │ │
│         │ └────────────────────────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────────────────────────────┘
```

### 10.2 Tablet 1024px (6 colunas, `px-4 gap-3`)

Hoje `lg` já está ativo em 1024px, o que produz três colunas de ~314px e agrava a lacuna. O V2 introduz a faixa 768–1279px como layout próprio.

```
┌────────────────────────────────────────────────────────┐
│ ☰  Visão Gerencial            [● API] 14:02            │  drawer no ☰
│ Período: 01–31/07/2026                                 │
│ [Canais 3▾][Marcas 5▾][Jul ▾][⇄]            (sticky)   │
├────────────────────────────────────────────────────────┤
│ ┌────────────────────────────────────────────────────┐ │
│ │ CONFIANÇA  3 canais · defasagem 1d · 2 avisos   ▸ │ │  faixa própria
│ └────────────────────────────────────────────────────┘ │
│ ┌──────────┬──────────┬──────────┐                     │
│ │ GMV      │ PEDIDOS  │ TICKET   │  KPIs: 3 + 2        │
│ │ ▲ 8,4%   │ Comp.    │ Comp.    │                     │
│ │ vs R$6,7M│ indispon.│ indispon.│                     │
│ ├──────────┼──────────┴──────────┤                     │
│ │ INVEST.  │ ROAS POR CANAL      │  ← ROAS ocupa 2 col:│
│ │ EM ADS   │ ML 4,2x · SH 3,1x   │    precisa de largura│
│ │ ML + SH  │ TK N/D              │    para 3 canais.   │
│ │ TK N/D   │                     │    Nenhuma célula   │
│ └──────────┴─────────────────────┘    vazia na fileira │
│ ┌──────────────────────┬──────────────┐                │
│ │ EVOLUÇÃO (dominante) │ PULSO        │  4 cols │ 2 cols│
│ │ [GMV|Pedidos]        │ ⚠ Custo alto │                │
│ │ min-h-[240px]        │ ⚠ Ads subut. │  finais         │
│ │ 1 série/canal        │ Ver todos ▸  │  alinhados      │
│ │ ⓘ série anterior     ├──────────────┤  (≤24px)        │
│ │   indisponível       │ CANAIS       │                │
│ └──────────────────────┴──────────────┘                │
│ ┌────────────────────────────────────────────────────┐ │
│ │ SAÚDE DO VOLUME POR CANAL                          │ │
│ │ 1 linha por canal, empilhadas; métricas em pares    │ │
│ │ ML: GMV · considerados / cancel. · taxa / dev. N/D  │ │
│ │ SH: GMV · considerados / cancel. · taxa / dev · taxa│ │
│ │ TK: GMV · registrados / cancel. N/D / devol. N/D    │ │
│ └────────────────────────────────────────────────────┘ │
│ ┌────────────────────────────────────────────────────┐ │
│ │ MATRIZ MARCA × CANAL  → overflow-x-auto            │ │
│ │ 1ª coluna sticky left-0 · TableScrollHint          │ │
│ └────────────────────────────────────────────────────┘ │
│ ┌───────────────────────┬────────────────────────────┐ │
│ │ MAIORES ALTAS         │ MAIORES QUEDAS             │ │
│ ├───────────────────────┴────────────────────────────┤ │
│ │ CONCENTRAÇÃO POR MARCA (largura total)             │ │
│ └────────────────────────────────────────────────────┘ │
│ ┌────────────────────────────────────────────────────┐ │
│ │ FILA DE ATENÇÃO (1 coluna, CTA abaixo do texto)    │ │
│ └────────────────────────────────────────────────────┘ │
└────────────────────────────────────────────────────────┘
```

### 10.3 Mobile 390×844 (1 coluna, `px-4 gap-3`)

Ordem reordenada para decisão: **atenção antes de detalhe**. A regra de alinhamento inferior entre cards **não se aplica** aqui (§7.2, item 6 do plano).

```
┌──────────────────────────────┐
│ ☰  Visão Gerencial      [●]  │
│ 01–31/07/2026                │
│ [Canais 3▾][Jul ▾]  (sticky) │
├──────────────────────────────┤
│ ┌──────────────────────────┐ │  faixa de confiança
│ │ CONFIANÇA · 2 avisos   ▸ │ │  (não é KPI)
│ └──────────────────────────┘ │
│ ┌────────────┬─────────────┐ │  KPIs 2×2 + 1
│ │ GMV        │ PEDIDOS     │ │
│ │ R$ 7,3M    │ 124.790     │ │
│ │ ▲ 8,4%     │ Comparação  │ │
│ │ vs R$ 6,7M │ indisponível│ │
│ ├────────────┼─────────────┤ │
│ │ TICKET     │ INVEST. ADS │ │
│ │ R$ 58      │ R$ 377k     │ │
│ │ Comparação │ ML+SH·TK N/D│ │
│ │ indisponív.│ (sem delta) │ │
│ ├────────────┴─────────────┤ │
│ │ ROAS POR CANAL           │ │  ← largura total
│ │ ML 4,2x · SH 3,1x        │ │
│ │ TikTok N/D  (sem delta)  │ │
│ └──────────────────────────┘ │
│ ┌──────────────────────────┐ │  1º  PULSO
│ │ PULSO           Atenção  │ │  (o que atender)
│ │ ⚠ Custo alto Shopee      │ │
│ │ ⚠ Ads subutilizado ML    │ │
│ │ Ver todos (7) ▸          │ │
│ │ 2 avisos de confiança ▸  │ │
│ └──────────────────────────┘ │
│ ┌──────────────────────────┐ │  2º  EVOLUÇÃO
│ │ EVOLUÇÃO [GMV|Pedidos]   │ │  min-h-[220px]
│ │ ▁▃▅█▆▃▅█▇▅▃              │ │  rótulos alternados
│ │ 01   10   20   31        │ │  1 série por canal
│ │ ⓘ série anterior indisp. │ │
│ └──────────────────────────┘ │
│ ┌──────────────────────────┐ │  3º  CANAIS
│ │ CANAIS · share GMV       │ │
│ └──────────────────────────┘ │
│ ┌──────────────────────────┐ │  4º  SAÚDE DO VOLUME
│ │ 1 canal por bloco,       │ │     POR CANAL
│ │ métricas empilhadas      │ │
│ │ TK: cancel./devol. N/D   │ │
│ └──────────────────────────┘ │
│ ┌──────────────────────────┐ │  5º  MATRIZ
│ │ MATRIZ  ← scroll →       │ │  1ª col sticky
│ └──────────────────────────┘ │  + TableScrollHint
│ ┌──────────────────────────┐ │  6º  MOVIMENTOS
│ │ [Altas|Quedas|Concentr.] │ │  abas, não 3 colunas
│ └──────────────────────────┘ │
│ ┌──────────────────────────┐ │  7º  FILA DE ATENÇÃO
│ │ FILA · CTA largura total │ │  alvos ≥44px
│ └──────────────────────────┘ │
└──────────────────────────────┘
   Drill-down = tela cheia (w-full h-full), não diálogo centrado
```

---

## 11. Mapa de drill-down

Formato: **origem → interação → explicação → evidência → CTA → destino → filtros preservados**. Todos abrem no **shell único** (`KpiDrilldownDialog`); nenhum modal empilhado; troca de visão por estado.

| # | Origem | Interação | Explicação (o quê + por quê) | Evidência | CTA | Destino | Filtros preservados |
|---|---|---|---|---|---|---|---|
| 1 | **Faixa de confiança** | clique | cobertura por canal, defasagem, avisos ativos | `data_warnings` com `source`, `last_date`, `staleness_days` | "Ver Regiões" / "Entender limitação" | `/regioes` ou permanece | `channels, brands, date_from, date_to, compare` |
| 2 | KPI **GMV** | clique | GMV do período, decomposição por canal, variação vs. anterior com valor nomeado | `EvidenceRow` por canal e por marca; `refreshed_at` da mesma resposta | "Ver detalhe por canal" | `/canais` | idem |
| 3 | KPI **Pedidos** | clique | decomposição por canal; ressalva de compradores (soma diária ≠ único no período) | `EvidenceRow` por canal; **sem** delta — "Comparação indisponível" declarada | "Ver pedidos por status" | `/pedidos` | idem |
| 4 | KPI **Ticket Médio** | clique | ticket = GMV ÷ pedidos no escopo; dispersão por canal | `EvidenceRow` por canal; **sem** referência | "Ver por marca" | permanece + destaque na matriz | idem |
| 5 | KPI **Investimento em Ads** | clique | investimento do período; cobertura **ML + Shopee**; TikTok `N/D` | `EvidenceRow` ML/Shopee; `DataQualityNote` do TikTok; **sem** delta | "Ver eficiência" | `/financeiro` | idem |
| 6 | KPI **ROAS por canal** | clique | ROAS de cada canal com ads, lado a lado; por que **não** existe ROAS consolidado | `EvidenceRow` por canal (ML, Shopee); `DataQualityNote` do TikTok; **sem** delta | "Ver eficiência" | `/financeiro` | idem |
| 7 | **Ponto/barra** da evolução | clique | o que aconteceu naquele dia: valor, posição na série, decomposição por canal | reusa `/overview`+`/brands` **com o período reduzido ao dia** | "Fixar este dia como período" | permanece, `date_from=date_to=dia` | canais/marcas mantidos; período **intencionalmente** alterado |
| 8 | **Legenda de canal** da evolução | clique | isola/realça a série do canal; se o canal falhou, diz **qual** falhou e que o total não está completo | série do canal; nota de falha parcial quando aplicável | "Abrir canal" | `/canais?channels=<canal>` | href explícito vence o global |
| 9 | **Barra de canal** (resumo) | clique | share do canal, GMV, variação | `EvidenceRow` do canal | "Abrir canal" | `/canais?channels=<canal>` | idem |
| 10 | **Linha de canal** em Saúde do volume | clique | métricas do canal com **unidade e definição** de cada uma; **definição da taxa declarada** (`cancelados / (não cancelados + cancelados)`); por que não há ranking entre canais (fonte, captura e semântica de status diferentes) | contagens e taxa servidas pelo contrato, sem recálculo; `DataQualityNote` para ML (devolução) e TikTok (ambas) | "Ver qualidade" | `/qualidade` | idem |
| 11 | **Célula da matriz** | clique | diagnóstico marca × canal: GMV vs. **mediana e p75 do próprio canal**, sinais com "por quê" | `EvidenceRow` de GMV/Ads/ROAS/ACOS/custo/frete vs. referência nomeada | "Abrir visão completa da marca" | `/brand/<marca>` **+ `ctx_*` do G3** | filtros + `ctx_from`, `ctx_signal`, `ctx_channel`, `ctx_brand` |
| 12 | **Cabeçalho de coluna** da matriz | clique | total e distribuição do canal entre marcas | `EvidenceRow` por marca | "Abrir canal" | `/canais?channels=<canal>` | idem |
| 13 | **Insight do Pulso** | clique | diagnóstico do G1/G2: métrica × referência, severidade explicada, nota de confiança | membros do grupo com valor e destino | CTA por membro | `/canais` ou `/brand/<marca>` | idem + `ctx_*` quando aplicável |
| 14 | **Item de Movimentos** | clique | variação **absoluta e percentual**, base do cálculo, piso aplicado | valores dos dois períodos | "Abrir marca" | `/brand/<marca>` + `ctx_*` | idem |
| 15 | **Marca em Concentração por marca** | clique | GMV da marca, participação no total selecionado, posição na concentração (Top 1/Top 3 quando houver base) | `EvidenceRow` por canal da marca | "Abrir marca" / "Ver Produtos" | `/brand/<marca>` ou `/produtos` | idem; ao ir para `/produtos`, **declara** que a tela tem contratos próprios por canal |
| 16 | **Linha da fila de atenção** | clique | alerta completo: impacto, evidência, confiança, ação | `metric_value` × `reference_value` (`reference_kind` nomeado), `source`, `last_date` | CTA do próprio insight (`href`) | conforme insight | via `mergeFilteredHref` |
| 17 | **Aviso de confiança** | clique | o que está indisponível, por quê, e o que **não** se pode concluir | fonte e data | "Entender" / "Ver Regiões" | `/regioes` ou permanece | idem |
| 18 | **Sinal** (chip de célula/insight) | foco ou clique | por que o sinal disparou (regra real, ex.: `>0` **e** `> mediana` **e** `≥ p75`) | referência do próprio canal | — | — | — |

### 11.1 Contrato transversal do drill-down

Ordem do G2 — **contexto → diagnóstico → evidência → limitação → ação**:

1. `DrilldownContextLine`: período + `refreshed_at` **da mesma resposta fresca** + filtros ativos.
2. Diagnóstico em linguagem humana (o quê **e** por quê). Chip de sinal nunca aparece mudo.
3. `EvidenceRow`: rótulo → valor tabular → referência **nomeada** (mediana/p75/período anterior/limiar).
4. `DataQualityNote` quando houver limitação — indisponibilidade é neutra, não crítica.
5. `DrilldownCta` com `min-h-11`, filtros preservados, **sem** repropagar `ctx_*`.

**Fonte, grão e período** devem ser **acessíveis** aqui (ou em tooltip/contexto), **sem** ocupar texto permanente em todos os cards. Rótulo permanente fica reservado a limitação material.

**A11y (obrigatória):** `role="dialog"` com rótulo, foco contido, `Escape`, `inert` no shell, retorno de foco ao gatilho, `aria-haspopup="dialog"` na origem. É exatamente onde superamos a referência, que tem **0** `role="dialog"` e **0** `aria-live` no repositório inteiro.

---

## 12. Representação de null, zero, indisponível e desatualizado

| Situação | Exibição | Cor | Nunca |
|---|---|---|---|
| Zero real medido | `R$ 0` / `0` | normal | — |
| `null` porque a fonte não fornece | `N/D` + "Não disponível nesta fonte" | cinza neutro | nunca `0`, nunca vermelho |
| Campo existe mas valor é estruturalmente inválido (cancelamento TikTok) | `N/D` + motivo | cinza neutro | **nunca zero** |
| `null` porque a requisição falhou | bloco em erro + "Tentar novamente" | rose | nunca dado antigo |
| **Falha parcial** (ex.: 1 de 3 séries de `/trend`) | séries disponíveis + canal ausente **nomeado**; total **não** declarado completo | amber | nunca fallback silencioso |
| Seção parcial | conteúdo + `DataQualityNote` nomeando o que faltou | amber | nunca silêncio |
| Dado obsoleto (`resolvedKey ≠ requestKey`) | bloco **não** renderiza valor | — | nunca valor do filtro anterior |
| Defasagem (`staleness_days`) | "dados até DD/MM" no contexto | amber se acima do limiar | nunca omitir |
| Sem comparação no contrato | "Comparação indisponível" | neutro | nunca delta calculado no cliente |
| Base insuficiente (concentração, movimentos) | não exibir o indicador | — | nunca aproximar |

---

## 13. Fronteiras desta especificação

**Não entra no V2-1:**

- **Composição monetária de qualquer tipo** — valor cancelado, valor devolvido, GMV anterior a exclusões, etapas monetárias exclusivas, funil.
- **Ranking de produtos** na Gerencial — escopo temporal incompatível entre canais (§8). Evolução futura dependente de contrato temporal uniforme.
- **Confiança como KPI** — é faixa própria (§2.2).
- **Delta em Pedidos, Ticket, Ads ou ROAS** — só GMV tem referência no contrato.
- **ROAS consolidado**, soma ou média de ROAS entre canais.
- **Ranking competitivo de cancelamento entre canais** — não por diferença de fórmula (ela é a mesma), mas por diferença de fonte, captura e semântica de status, e por ausência de cobertura confiável no TikTok (§6, regra 4).
- **Recálculo de qualquer taxa com outro denominador** — a taxa servida pelo endpoint é a única exibida.
- **Série comparativa e granularidade selecionável** na evolução — indisponíveis, declaradas em texto; extensão aditiva fica no V2-2.
- Dados do Data Mart (`/tempo-real`, `/inteligencia`, `/operacoes`, seção mensal de Marca — G4); margem, CMV, devolução ML; cancelamento/devolução TikTok como número; pedido individual; qualquer escrita; meta/orçamento; qualquer conceito B2B da referência; alteração de `DESIGN.md`.

**Nenhuma decisão de produto permanece aberta.** Bloco dominante (Evolução Temporal, 7/5 colunas) e faixa de KPIs estão fechados neste documento.

---

## 14. Registro de implementação (Gate V2-1)

**Status: implementado, aguardando revisão.** Somente a rota `/` e seus
componentes/helpers diretos foram tocados. Zero backend, zero endpoint novo,
zero dependência, zero banco/pipeline/Airflow/Scheduler, zero deploy.

### 14.1 Arquitetura final

`app/page.tsx` (494 linhas) é **coordenador**: filtros, identidade das
requisições (delegada ao hook), derivação via helpers puros, composição dos oito
blocos e o estado do único diálogo. Nenhuma regra de negócio no JSX.

| Camada | Arquivos |
|---|---|
| Lógica pura (testável sem React) | `src/lib/gerencial/{request-key,trend-series,kpi-band,volume-health,brand-matrix,attention,channel-colors}.ts` |
| Coordenação de fontes | `src/hooks/useGerencialSources.ts` |
| Blocos | `src/components/gerencial/{GerencialHeader,ConfidenceStrip,KpiBand,EvolutionCard,EvolutionChart,PulseChannelsColumn,VolumeHealthCard,BrandChannelMatrix,MovementsPanels,AttentionQueue}.tsx` |
| Conteúdos de drill-down | `src/components/gerencial/GerencialDrilldowns.tsx` |

**Reuso, não duplicação:** `KpiDrilldownDialog` (shell único), os quatro
primitives do G2, `PulsoPeriodoPanel`, `ChannelComparisonDialogContent`
(células da matriz), `TableScrollHint`, `computeRequestStatus`,
`gmvChannelBreakdown`, `mergeFilteredHref`, `useGlobalFilters`.
`KpiCard` **não** foi tocado — é consumido por sete outras rotas, e a faixa nova
usa um componente próprio. `KpiDrilldownContent` e `KPI_META` foram **estendidos**
de forma aditiva com `ad_spend`.

**Obsoletos na Gerencial (mantidos no repositório):** `TrendChart`,
`ChannelPerformancePanel`, `BrandPerformanceTable`. Dois deles são asseverados
por testes estáticos preexistentes (`regioes-map`, `global-filters`,
`scroll-hint`), então removê-los sairia do escopo deste gate — ficam como dívida
de limpeza.

### 14.2 Achados do próprio QA, corrigidos

1. **Fallback de mock passando por dado live.** Os fetchers não rejeitam: sem
   API, devolvem mock com `live: false`. Um `/quality` em falha renderizava
   números mockados de cancelamento ao lado de KPIs reais. Corrigido em
   `toSource`: fora do modo demonstração, `live: false` é indisponibilidade da
   fonte. O modo demonstração continua sendo decidido pelo overview, com a
   página inteira rotulada — contrato herdado das 11 rotas.
2. **Overflow horizontal de 5px no mobile.** O `<button>` do card de KPI se
   dimensionava pelo conteúdo e estourava a trilha da grade; corrigido com
   `w-full min-w-0`. Medido antes (`scrollWidth` 395 em viewport 390) e depois
   (390 = 390).
3. **`recharts` no bundle inicial.** Os cards importavam `CHANNEL_STROKE` de
   `EvolutionChart`, e o import estático anulava o `next/dynamic`. As cores
   foram movidas para `channel-colors.ts`: First Load da rota `/` caiu de
   **252 kB para 144 kB**.

### 14.3 Medições de alinhamento (bounding box, runtime)

Critério de §7.2 do plano: diferença ≤24px entre o final visual do card de
Evolução e do item Pulso+Canais.

| Viewport | 3 canais | só TikTok | só ML | só Shopee |
|---|---|---|---|---|
| Desktop 1440×900 | **0px** | **0px** | **0px** | **0px** |
| Tablet 1024×768 | **0px** | **0px** | **0px** | **0px** |
| Mobile 390×844 | n/a (empilhado) | — | — | — |

Medido de novo com todas as seis fontes resolvidas (coluna direita crescendo de
452px para 642px no desktop): **0px**. Zero overflow horizontal nos três
viewports. Zero linha órfã por `row-span` — o `row-span` foi eliminado.

### 14.4 Limitações declaradas

- `/executive-summary` responde em **2,86s** contra ~0,7s das outras cinco
  fontes (medido localmente). É exatamente o que justifica estados
  independentes: KPIs, evolução, volume e matriz pintam antes; Pulso, faixa de
  confiança e fila de atenção chegam depois, cada um com o próprio skeleton.
- A série comparativa e a granularidade selecionável seguem **indisponíveis** e
  declaradas em texto — extensão aditiva de `/trend` fica no V2-2.
- Dois `<h1>` na página (shell + Gerencial) — dívida preexistente **U6-04**, não
  introduzida aqui; corrigi-la exige tocar o shell, fora do escopo.
- `text-[11px]`/`text-[10px]` ficam fora da rampa do `DESIGN.md`; ver §8.2 do
  plano. Prática preexistente em 16 e 22 arquivos, mantida por consistência.

### 14.5 Itens do spec que NÃO foram implementados

Registrados aqui para não haver divergência entre desenho e código:

1. **Elo entre insight e ponto da série** (§5): o desenho previa destacar, no
   gráfico, o ponto do `last_date` de um insight em foco. **Não implementado.**
   Exigiria elevar o estado de foco do Pulso até o gráfico, atravessando dois
   blocos, e o ganho não justifica o acoplamento nesta rodada. O restante do §5
   está implementado.
2. **`max-h` com rolagem interna na lista de insights** (§5): desnecessário na
   prática — `buildPulse` já limita `pulse.top` a três itens, então a lista é
   limitada por construção. Os painéis de Movimentos e Concentração, que podem
   crescer, **têm** o `max-h` com rolagem.

Nenhum dos dois afeta os contratos de dado, os estados ou a acessibilidade.

---

## 15. Rodada consolidada de correção (V2-1, única permitida)

**Status: corrigido, aguardando revisão.** Uma revisão estrita encontrou nove
findings — dois funcionais graves, quatro lacunas de contrato e três de
qualidade. Todos corrigidos nesta rodada. Não haverá outra.

### 15.1 Findings e correções

| # | Finding | Correção |
|---|---|---|
| **A** | O ramo `overviewStatus.error \|\| isEmpty` envolvia quase todos os blocos: uma falha só do `/overview` apagava evolução, Pulso, matriz, movimentos e fila, contradizendo o contrato do próprio hook | Gate global **removido**. A estrutura fica sempre montada; cada bloco recebe o estado da própria fonte. `KpiBand` ganhou estado de erro; o `aria-live` passou a **nomear** as fontes indisponíveis em vez de dizer "Dados carregados" |
| **B** | Quatro caminhos de drill-down do spec estavam ausentes | Implementados: legenda de canal (com isolamento visual da série), cabeçalho de canal da matriz, concentração por marca (explica antes de navegar) e chips de sinal na célula |
| **C** | Falha de `/canais` apagava a matriz, que depende de `/brands` para o GMV | Estado **parcial**: GMV/share/variação permanecem, sinais e referências são declarados indisponíveis, e a célula aberta informa exatamente o que falta. Só erro de `/brands` bloqueia a matriz |
| **D1** | O detalhe do ponto imprimia números **mockados** com uma nota de demonstração | Guarda de mock: em página live, resposta não-live é indisponibilidade. `overview` e `brands` do recorte ganharam estados independentes, com parcial nomeando a fonte ausente |
| **D2** | Em grão mensal o CTA dizia "Fixar este dia" e aplicava `date_from = date_to = bucket.date`, reduzindo o mês a um dia | `bucketRange()` puro, sem `Date` (logo sem erro de fuso). Grão mensal aplica o primeiro e o **último dia real** do mês |
| **E** | A nota de cobertura de Ads citava sempre "Mercado Livre e Shopee", mesmo com um só selecionado | `adsCoverageNote()` deriva da seleção; as sete combinações estão cobertas em teste. Ausência de valor continua `N/D`, nunca R$ 0 |
| **F** | A faixa afirmava "cobertura" a partir de `gmv != null` | Passou a afirmar **disponibilidade de série**, derivada dos estados por canal. §15.2 |
| **G** | O diálogo só fechava quando o filtro mudava pelos controles da página | Efeito sobre a **identidade dos filtros efetivos**: fecha por back/forward e URL externa também. Trocar a métrica não fecha nem refaz fetch |
| **H** | 26 ocorrências novas de fonte abaixo de 12px | Todas elevadas a ≥12px nos arquivos do V2. Dívida realmente preexistente (16 arquivos com 11px, 22 com 10px, 3 com 9px) **não** foi tocada |

### 15.2 Semântica final da faixa de confiança

A faixa **não** infere cobertura de valor. Ela reporta o estado da série de cada
canal, em quatro valores distintos:

| Estado | Significado | Origem |
|---|---|---|
| `checking` | verificando | série ainda carregando |
| `available` | série disponível | resposta fresca **com** registros — inclusive registros de valor zero |
| `no_records` | sem registros no período | resposta fresca **sem** nenhuma linha |
| `unavailable` | série indisponível | a fonte daquele canal falhou |

Texto da faixa: "Série disponível em X de Y canais", mais "N sem registros" e
"N indisponíveis" quando houver. Defasagem, avisos e fontes nomeadas continuam
vindo do `/executive-summary`, **separadamente**: se ele não responder, a faixa
mantém a disponibilidade das séries e declara que defasagem e avisos **não foram
verificados** — o que é diferente de afirmar que não existem.

O drill-down diz o limite explicitamente: disponibilidade de série **não**
comprova completude do dado.

### 15.3 Achado adicional do QA — modo demonstração

Decidir o modo demonstração apenas pelo `/overview` produzia um estado misto:
com `/overview` fora do ar e as demais fontes live, a página exibia **KPIs
mockados ao lado de matriz e evolução reais**, com só um badge global. A regra
passou a exigir que **todas** as fontes com fallback tenham caído para mock; se
ao menos uma respondeu de verdade, a que caiu é tratada como indisponível —
inclusive o próprio `overview`.

### 15.4 Contratos finais

**Detalhe do ponto (live × mock):**

| Página | Resposta | Comportamento |
|---|---|---|
| live | live | números exibidos |
| live | não-live (mock) | **indisponível** — números não são renderizados |
| live | uma fonte live, outra não | evidência da que respondeu + nota parcial **nomeando** a ausente |
| live | ambas não-live | erro do detalhe; a série já carregada continua válida |
| demonstração | mock | exibido, com o bloco rotulado como demonstração |

**CTA do bucket:** grão diário → "Fixar este dia como período", `date_from = date_to = dia`. Grão mensal → "Fixar este mês como período", primeiro ao último dia real do mês (fevereiro comum, bissexto, regra do século e dezembro cobertos em teste). Canais, marcas e comparação preservados nos dois casos.

**Cobertura de Ads pelas sete combinações:**

| Seleção | Nota |
|---|---|
| TikTok | "Sem cobertura de mídia na seleção · TikTok Shop: não disponível nesta fonte" |
| ML | "Cobertura: Mercado Livre" |
| Shopee | "Cobertura: Shopee" |
| ML + Shopee | "Cobertura: Mercado Livre e Shopee" |
| TikTok + ML | "Cobertura: Mercado Livre · TikTok Shop: não disponível nesta fonte" |
| TikTok + Shopee | "Cobertura: Shopee · TikTok Shop: não disponível nesta fonte" |
| os três | "Cobertura: Mercado Livre e Shopee · TikTok Shop: não disponível nesta fonte" |

### 15.5 Matriz fonte × bloco

| Bloco | Fontes | Comportamento em falha |
|---|---|---|
| Faixa de confiança | séries `/trend` + `/executive-summary` | disponibilidade das séries sobrevive; avisos ficam "não verificados" |
| 5 KPIs | `/overview` | bloco em erro com retry; nada mais é afetado |
| Evolução | até 3 × `/trend` | canal nomeado, total não desenhado, demais séries ficam |
| Pulso | `/executive-summary` | painel de indisponibilidade; resumo de canais preservado |
| Resumo de canais | `/overview` | painel de indisponibilidade; Pulso preservado |
| Saúde do volume | `/quality` + `/overview` | bloco declara a fonte ausente; demais blocos ficam |
| Matriz — GMV/share/variação | `/brands` | só `/brands` bloqueia a matriz |
| Matriz — sinais e referências | `/canais` | **parcial**: grade fica, sinais declarados indisponíveis |
| Movimentos e Concentração | `/brands` | bloco em erro com retry |
| Fila de atenção | `/executive-summary` | duas listas com estado próprio |

### 15.6 Drill-downs concluídos — 16 tipos de acionamento

Contando cada elemento acionável como um tipo, são **16**. Os cinco KPIs contam
como **cinco** tipos, porque cada card tem conteúdo, decomposição e regra de
comparação próprios:

| # | Acionamento |
|---|---|
| 1–5 | KPI de GMV, Pedidos, Ticket Médio, Investimento em Ads e ROAS por canal |
| 6 | Faixa de confiança no dado |
| 7 | Ponto da série (bucket) |
| 8 | **Legenda de canal da evolução** |
| 9 | Barra de canal no resumo |
| 10 | Linha de canal em Saúde do volume |
| 11 | **Cabeçalho de canal da matriz** |
| 12 | Célula da matriz (marca × canal) |
| 13 | Insight do Pulso (detalhe e "ver todos") |
| 14 | Item de Movimentos |
| 15 | **Marca em Concentração** |
| 16 | Linha da fila de atenção — comercial e aviso de dado |

Uma contagem por *conteúdo distinto de diálogo* daria um número menor, porque os
cinco KPIs compartilham o mesmo componente de conteúdo; para evitar ambiguidade,
a referência oficial deste documento é **16 tipos de acionamento**.

Todos no **shell único**, com foco contido, `Escape`, retorno de foco ao gatilho
e exatamente um diálogo visível — verificado por teclado nos quatro caminhos
novos. Os chips de sinal são `<span>` dentro do botão da célula (nunca botão
aninhado) e o nome acessível da célula anuncia os sinais.

---

## 16. Reparação de stop-loss (V2-1, pré-commit)

Cinco inconsistências encontradas na revisão da rodada consolidada. Escopo
estritamente limitado a elas — não é uma nova rodada de produto.

### 16.1 Cálculo final do modo demonstração

A regra saiu do hook para um módulo puro e testável,
`src/lib/gerencial/demo-mode.ts`. A versão anterior errava de duas formas
silenciosas: `every` sobre uma **lista filtrada** é vacuamente verdadeiro — bastava
o `/overview` mock concluir primeiro para a página virar "demonstração" enquanto as
demais fontes ainda carregavam — e `Object.values(seriesState)` incluía canais fora
da seleção e chaves de requisições antigas.

`decideDemoMode` avalia o **conjunto esperado** da requisição atual: as quatro
fontes agregadas com fallback (`overview`, `brands`, `canais`, `quality`) mais **uma
série por canal selecionado**. Cada uma precisa de `loading=false`,
`errored=false`, `resolvedKey` igual à chave esperada (a do canal, para séries) e
`live=false`. O `/executive-summary` não entra: não tem fallback mock, ele falha de
verdade.

Cada fonte esperada é classificada em **quatro** estados — `pending` (ausente,
carregando ou com chave antiga), `live`, `mock` e `terminal_error` (chave atual,
concluída com erro) — e a decisão segue esta precedência:

1. alguma `live` → `demoMode=false`, `pending=false`;
2. alguma `terminal_error` → `demoMode=false`, `pending=false`;
3. todas `mock` → `demoMode=true`, `pending=false`;
4. resta alguma `pending` → `demoMode=false`, `pending=true`.

O estado `terminal_error` existe por um bug real encontrado na revisão final: um
erro era tratado como espera, então `pending` ficava verdadeiro para sempre. Como
`pending` converte os mocks das outras fontes em carregamento neutro,
`sources.anyLoading` nunca encerrava e a interface podia ficar em "Atualizando…"
indefinidamente. **Erro é uma conclusão**: com ele, a demonstração já não pode ser
confirmada, logo não há o que esperar — e as fontes em mock passam a indisponíveis.
Um erro com `resolvedKey` antigo **não** é terminal para a requisição nova.

Enquanto `pending` é verdadeiro, uma fonte cujo mock foi substituído fica em estado
**neutro de carregamento** — não exibe os números mockados nem afirma
indisponibilidade definitiva.

| Cenário | `demoMode` | `pending` | Efeito na interface |
|---|---|---|---|
| Só `/overview` mock concluiu; demais carregando | `false` | `true` | KPIs em carregamento neutro; **nenhum número mock** |
| Quatro agregadas mock; série ainda carregando | `false` | `true` | idem |
| Todas as fontes atuais mock | `true` | `false` | mock coerente, página rotulada como demonstração |
| Uma live e as demais mock | `false` | `false` | fontes mockadas declaradas **indisponíveis** |
| Erro **terminal** numa fonte esperada (agregada ou série), com a chave atual | `false` | `false` | decisão **tomada**: as fontes em mock viram indisponíveis, e `anyLoading` encerra |
| Erro com `resolvedKey` **antigo** | `false` | `true` | erro de outra requisição não conclui a atual |
| `resolvedKey` antigo (agregada ou série) | `false` | `true` | decisão não é reaproveitada |
| Série de canal **não** selecionado, live | irrelevante | — | não influencia a decisão |
| Série de canal selecionado ausente | `false` | `true` | nunca confirma |
| Troca de filtro | `false` | `true` | a decisão anterior é descartada |

### 16.2 Quatro estados do detalhe da célula da matriz

`matrixRow` ausente tem quatro causas, e antes as quatro produziam a mesma frase
("indisponível nesta carga"), o que culpava a fonte inclusive quando ela respondeu
bem:

| Estado de `/canais` | Mensagem |
|---|---|
| `loading` | "Verificando sinais e referências comparativas deste canal…", com `role="status"` e `aria-busy` |
| `error` | "Fonte de sinais e referências indisponível nesta carga." |
| `fresh` + demonstração + sem linha | "O modo demonstração não modela Ads, custos, frete, sinais ou medianas por marca × canal." |
| `fresh` live + sem linha | "Não há registro comparativo para {marca} × {canal} no período." — e diz explicitamente que **não indica falha de carga** |

Com `matrixRow` presente, o comportamento anterior é preservado: o diálogo
comparativo completo.

### 16.3 Sinal desconhecido

`signalLabel` devolvia o próprio identificador como fallback, então um
`snake_case` novo do backend podia aparecer cru na interface — inclusive no
`headline` de Canais. Agora devolve **"Sinal não mapeado"**; o identificador
permanece apenas no contrato, e `isUnmappedSignal()` existe para diagnóstico e
teste sem renderizar nada. A correção vale também para `/canais` e para o diálogo
comparativo, que compartilham o helper.

### 16.4 Tipografia — varredura ampliada

`EvolutionChart` ainda passava `tick={{ fontSize: 11 }}` nos dois eixos: estilo
inline do Recharts, invisível para uma busca por classes Tailwind. Ambos foram para
12px, e a verificação passou a cobrir três formas — `text-[Npx]`, `fontSize: N` e
`font-size: Npx` — em todos os arquivos do V2. O QA mede o tamanho **renderizado**
dos ticks no navegador: 13 ticks, nenhum abaixo de 12px.

### 16.5 Contagem de drill-downs

Ver §15.6: a referência oficial passou a ser **16 tipos de acionamento**, com o
critério explicado (os cinco KPIs contam como cinco).
