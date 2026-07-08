# Filtros Globais — Auditoria (Gate 1) e Contrato (Gate 2)

Atualizado: 2026-07-07

## Gate 1 — Auditoria somente leitura (estado antes desta feature)

Mapeamento feito por leitura direta de `apps/web/app/**/page.tsx`, `apps/web/src/components/{MarketplaceFilter,PeriodSelector,AppNav}.tsx`, `apps/web/src/lib/api-client.ts`, `apps/api/app/routers/performance.py`, `apps/api/app/services/performance_service.py`, `apps/api/app/schemas/performance.py`, e cruzado com `docs/kpi_dictionary.md`, `docs/gold_vs_marts_matrix.md`, `docs/data_contracts.md`.

### Matriz completa

| Tela | Canal | Marca | Período | Persistência URL | Comparação | Estados | Endpoints | Fonte | Grão | Testes |
|---|---|---|---|---|---|---|---|---|---|---|
| Gerencial (`/`) | MultiSelect (Todos/TK/ML/Shopee) | ✗ | Preset mensal (`ref_month`) | Não | MoM (API, `gmv_mom_pct`) | loading/erro/retry/mock | `/overview`, `/brands`, `/monthly` | `marts.fact_marketplace_daily_performance` | Mensal | Nenhum |
| Canais | MultiSelect | ✗ | Preset mensal | Não | Não exposta | Skeletons, erro/retry | `/canais` | idem | Mensal × brand | Nenhum |
| Financeiro | MultiSelect | ✗ | Preset mensal | Não | Não exposta | opacity loading, erro/retry | `/financeiro` | idem | Mensal × brand | Nenhum |
| Qualidade | MultiSelect | ✗ | Preset mensal | Não | MoM ML (via `gold.ml_gestao_mensal`, não Neon) | opacity loading, erro/retry | `/quality` | idem + `gold.ml_gestao_mensal` (RDS) | Mensal × brand | Nenhum |
| Pedidos | **Nenhum filtro de canal** | **Nenhum** | **`days_back`** (7/14/30, botões) — não usa mês | Não | Não | loading/erro/retry | `/pedidos?days_back=` | idem (TikTok+ML apenas, sem Shopee) | Diário + agregado | Nenhum |
| Produtos | **Tabs** (não multi-select), 1 canal por vez | Select por aba (preserva entre trocas quando existe no canal) | ML=acumulado (sem período); TikTok/Shopee=preset mensal | Não | Não | loading por canal, erro silencioso | `/produtos/{ml,tiktok,shopee}[/summary]` | `marts.fact_ml_produto_ranking`, `fact_tiktok_product_daily`, `fact_shopee_product_monthly` | Ranking atual (ML) / mensal (TK, SH) | 6 arquivos (produtos) |
| Tempo Real | ✗ (TikTok fixo) | Buttons (Todos + marca) | **"hoje" fixo**, auto-refresh 5min | Não | vs ontem (delta%) | loading, offline explícito | `/tempo-real` | `gold.tiktok_shop_hourly` (RDS) | Horário | Nenhum |
| Inteligência | ✗ (ML fixo) | Buttons (filtra só Urgente/Escalar/Testar Ads) | 30d fixo | Não | Não | opacity loading, erro/retry | `/inteligencia` | `gold.ml_produto_ranking`, `gold.ml_cross_company_summary`, `gold.tiktok_product_daily` (RDS) | Produto + agregado | Nenhum |
| Operações | ✗ | Buttons parcial (só Criadores) | 7d/30d fixos | Não | Alertas dinâmicos | opacity loading, erro/retry | `/operacoes` | `gold.ml_gestao_diaria` (RDS) | Diário + 7d agregado | Nenhum |
| Brand Detail (`/brand/[brand]`) | MultiSelect (`/daily`) | Fixa via rota (`[brand]`) | Preset mensal (mensal TikTok) + `/daily` sempre 60 dias fixos | Brand via rota | MoM client-side (30d vs 30d anteriores) | mock fallback | `/daily`, `/brand-detail` | `gold.tiktok_brand_daily` (RDS) — TikTok-only | Mensal + diário | Nenhum |

### Achados transversais

1. **Nenhuma tela persiste filtros na URL** (exceto `brand` via rota em Brand Detail) — reload ou deep link perde o estado.
2. **Marca nunca filtra as 4 telas agregadas principais** (Gerencial/Canais/Financeiro/Qualidade) — só existe seleção de marca em Produtos/Tempo Real/Inteligência/Operações/Brand Detail, cada uma com mecanismo próprio (nenhum reaproveita componente comum).
3. **Período heterogêneo**: mês (`ref_month`, 6 telas) vs dias (`days_back`, Pedidos) vs hoje fixo (Tempo Real) vs acumulado sem período (Produtos ML).
4. **SQL**: `performance_service.py` (Neon, usado pelas 6 telas do Gate 3) é 100% parametrizado (`text()` + bind params, `= ANY(:mkt_ids)`). `gold_service.py` (RDS, usado por Tempo Real/Brand Detail/Inteligência/Operações e pela metade MoM-ML de Qualidade) usa f-strings com `_fmt_list()` — não explorável via HTTP hoje porque os valores vêm de listas hardcoded (`BRANDS_IN_SCOPE`/`ML_BRANDS`) ou de `brand` já validado contra allowlist antes de chegar lá, mas é dívida técnica pré-existente registrada aqui e **não corrigida nesta feature** (não mexemos em `gold_service.py`).
5. **Validação de datas**: nenhum endpoint valida limite de intervalo em `ref_month`/datas nem rejeita datas futuras; `months_back`/`days_back` têm limites (`[1,24]`/`[7,90]`/`[7,365]`) mas `ref_month` não.
6. **`ml_unique_buyers` em `/overview`** vem de `gold.ml_gestao_mensal` (deduplicado), não do agregado diário do Neon — pode divergir ligeiramente de `/brands`/`/canais` (soma diária, sobrestima). Pré-existente, documentado em `docs/gold_vs_marts_matrix.md`.

---

## Gate 2 — Contrato de filtros (implementado nesta feature)

### Parâmetros aceitos (novos, com compatibilidade)

| Parâmetro | Tipo | Aceito em | Substitui |
|---|---|---|---|
| `channels` | `"all"` \| canal isolado \| lista separada por vírgula | overview, brands, canais, financeiro, quality, pedidos, daily, brand-detail (só validação) | `marketplace` (continua aceito como alias legado) |
| `brands` | lista de `brand_key` separada por vírgula | overview, brands, canais, financeiro, quality, pedidos | — (novo) |
| `date_from` / `date_to` | `YYYY-MM-DD`, inclusivos | overview, brands, canais, financeiro, quality, pedidos, daily | `ref_month` (continua aceito como alias legado) / `days_back` (idem) |
| `compare` | `true`/`false` | overview, brands, canais, financeiro, quality, pedidos | — (novo, opt-in) |

**Precedência (nunca mistura silenciosa)**: `date_from`+`date_to` > `ref_month` > default do endpoint (mês anterior nas telas mensais; últimos 30 dias em Pedidos). `channels` > `marketplace`. Enviar só um de `date_from`/`date_to` retorna 422.

**Validações**: `date_from <= date_to`; intervalo máximo 366 dias; `date_to` não pode exceder hoje+1; `brands` validado contra `marts.dim_loja` (marca inexistente ou inativa → 422).

**Resposta** (campos novos, adicionados sem remover os antigos): `date_from`, `date_to`, `compare_date_from`/`compare_date_to` (quando `compare=true`), `filters.channels`, `filters.brands`, `refreshed_at` (`MAX(ingested_at)` do escopo filtrado).

### Exceções documentadas (não recebem o contrato completo)

- **Tempo Real**: continua "hoje" fixo — não aceita `date_from`/`date_to` (semântica de tempo real, ver Gate 1 achado 3). Aceita filtro de marca (já existente).
- **Produtos**: mantém tabs + select próprio — ML não tem competência mensal na fonte (`fact_ml_produto_ranking` é ranking acumulado), então não recebe seletor de data unificado. Fora do escopo desta rodada.
- **Inteligência / Operações**: fora do escopo desta rodada — continuam com filtros próprios parciais, backend em `gold.*`/RDS.
- **Brand Detail**: marca fixa pela rota, nunca multi-select; `channels` aceito em `/daily` (filtra de fato) e em `/brand-detail` apenas para validação — a fonte é TikTok-only, o parâmetro não muda o resultado.

Ver plano de implementação completo em `C:\Users\Notebook\.claude\plans\quiet-crafting-rain.md` (Gate 2/3) para o detalhamento arquivo a arquivo.

---

## Gate 3 — Default por rota e regra "URL explícita vence" (correção de regressão)

Achado da revisão de código do Gate 2: o frontend passou a mandar `date_from`/`date_to` concretos em **toda** requisição (inclusive na entrada "vazia", via fallback interno para `presetRange("30d")`), o que fazia o backend nunca mais exercer o branch de "mês calendário anterior" — o default efetivo virou silenciosamente "últimos 30 dias" nas 4 telas mensais, e o MoM automático de Gerencial/Qualidade sumiu por padrão. Corrigido nesta rodada.

### Defaults por tela (entrada direta, sem nenhum parâmetro de filtro na URL)

| Tela | `defaultPreset` | `defaultCompare` | Materializa na URL como |
|---|---|---|---|
| Gerencial (`/`) | `mes_anterior` | `true` | `channels=all&date_from=<1º dia>&date_to=<último dia>&compare=true` |
| Canais | `mes_anterior` | `false` (padrão) | idem, sem `compare` |
| Financeiro | `mes_anterior` | `false` (padrão) | idem, sem `compare` |
| Qualidade | `mes_anterior` | `true` | idem Gerencial |
| Brand Detail (`/brand/[brand]`) | `mes_anterior` | `true` (comparação client-side na seção diária) | idem Gerencial |
| Pedidos | `30d` | `false` (padrão) | `channels=all&date_from=<hoje-29>&date_to=<hoje>` (sem `compare`) |

"Últimos 30 dias" continua disponível como preset manual em qualquer tela — só deixou de ser o default de Gerencial/Canais/Financeiro/Qualidade/Brand Detail.

### Regra: querystring explícita sempre vence

`useGlobalFilters({ defaultPreset, defaultCompare })` só aplica o default da tela quando a URL não tem **nenhum** parâmetro de filtro reconhecido (`channels`, `marketplace`, `brands`, `date_from`, `date_to`, `compare`). Assim que qualquer um desses existir — por materialização do próprio default, por navegação entre telas (ex: Gerencial → Pedidos preservando o período), ou por edição manual da URL — os campos ausentes caem no fallback **neutro** do parser (`channels=all`, `compare=false`), nunca no default da tela. É isso que garante:

- **`compare=false` sobrevive a reload**: desligar a comparação remove o parâmetro `compare` da URL; como `date_from`/`date_to` continuam presentes, um reload não volta a aplicar `defaultCompare=true`.
- **Navegação entre telas preserva o período**: ir de Gerencial (mês anterior) para Pedidos via link não reaplica "últimos 30 dias" — o período chega explícito na querystring.
- **O default nunca é um "peso morto" recalculado a cada render**: é materializado uma única vez via `router.replace` (ver `apps/web/src/hooks/useGlobalFilters.ts`).

### Backend: `ref_month` é preenchido também a partir de `date_from`/`date_to`

Para que a materialização acima (que sempre usa `date_from`/`date_to`, nunca `ref_month=YYYY-MM`) continue disparando o MoM de mês-calendário-completo já auditado, `resolve_period` (`apps/api/app/deps/period.py`) agora reconhece quando um intervalo explícito cobre exatamente um mês calendário e preenche `EffectivePeriod.ref_month` automaticamente — o mesmo efeito de ter enviado `ref_month=YYYY-MM` diretamente. Isso também corrige de brinde o mesmo bug para qualquer cliente que clique manualmente no preset "Mês anterior".

Efeito em `get_overview` / `get_brands` / `get_quality` (os três que calculam `*_mom_pct`): `compare=true` sobre um mês calendário completo usa o **mês calendário anterior de verdade** (`_prev_month` + `_month_bounds`), não a janela de N dias corridos de `resolve_previous_period` — as duas divergem numericamente sempre que os meses vizinhos têm contagens de dias diferentes (10 de 12 pares consecutivos). Sem `compare=true`, nenhuma comparação é calculada, mesmo para um mês completo — o toggle da UI controla isso de ponta a ponta.
