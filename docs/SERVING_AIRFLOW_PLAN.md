# Camada de serving Data Mart → Neon → Render → futuro Airflow

**Gate S0, Task 1/3 — CONCLUÍDA e APROVADA. 11/08/2026.**
Blueprint entregue, **zero implementação**: nenhuma DAG, nenhum schema, nenhum sync,
nenhum endpoint, nenhuma migração. Auditoria read-only, uma rodada consolidada de
correção consumida. **O Gate S1 não foi iniciado.**

Resultados centrais fixados por este documento:

- **quatro** endpoints realmente indisponíveis em produção — `/brand-detail`,
  `/tempo-real`, `/inteligencia` e `/operacoes` (§1);
- **quatro** tabelas novas em S1–S3: `fact_ml_gestao_diaria`,
  `fact_tiktok_brand_content_daily`, `fact_tiktok_creator_daily` e
  `fact_ml_cross_company_summary` (§4);
- **duas fatos existentes de reuso obrigatório**: `marts.fact_ml_produto_ranking` e
  `marts.fact_tiktok_product_daily` — nenhuma tabela duplicada, nenhuma segunda
  verdade para os mesmos produtos (§3.5);
- a fato de produtos TikTok receberá, no futuro, **apenas migration aditiva** de
  `active_videos` e `video_views`, junto da **extensão explícita** da lista de
  colunas de `pipelines/sync_produtos.py` (§3.5, §25 Gate S3);
- **nenhuma cópia indiscriminada da Gold**: lista de colunas explícita e versionada,
  zero `SELECT *` (§4.2);
- **`/tempo-real` permanece fora de S1–S3** e **não deve ser declarado resolvido**
  (§25.3);
- o **Airflow existe** segundo o proprietário, mas **repositório, hospedagem e
  conectividade não foram acessíveis nesta sessão** (§24);
- **piloto manual não prova a conectividade do worker** — são dois resultados
  distintos (§24.1).

---

## 1. Problema atual

Quatro endpoints da API leem `gold.*`/`raw.*` no **Data Mart (RDS)**. O roteamento é
automático: `gold_service._uses_datamart(sql)` detecta os prefixos `gold.`/`raw.` e
envia a consulta ao `datamart_engine`, em vez do engine principal (Neon).

O RDS **exige VPN**. O Render não a tem. Consequência medida em produção
(11/08/2026, `curl` direto, fora do navegador):

| Endpoint | Status em produção |
| --- | --- |
| `/api/v1/performance/brand-detail?brand=barbours` | **500** |
| `/api/v1/performance/tempo-real` | **500** |
| `/api/v1/performance/inteligencia` | **500** |
| `/api/v1/performance/operacoes` | **500** |

O Gate G4 já havia diagnosticado a causa raiz — **ausência de conectividade, não
consulta lenta**: com VPN as cinco consultas de `get_brand_detail` rodam em 4,07s;
sem VPN o tempo é 100% tempo de conexão, 0 byte recebido.

**Correção do smoke anterior.** O smoke pós-publicação de 10/08/2026 reportou 500 em
apenas três superfícies e tratou `/brand-detail` como saudável. **Estava errado, e o
erro era do instrumento:** a passagem rápida esperava 2,2s por rota, e o request de
`brand-detail` — disparado depois do `/daily` na página de Marca — não concluía nessa
janela. Com 9s de espera ele aparece: `net::ERR_FAILED`, com o mesmo rótulo de CORS
dos outros três. **As quatro superfícies do G4 seguem dependentes do Data Mart e as
quatro falham.** O G4 estava certo.

**Por que o navegador diz "CORS".** A resposta 500 do FastAPI não carrega
`Access-Control-Allow-Origin`, então o browser bloqueia a leitura e rotula como
política de CORS. O CORS está correto: `/overview` e `/canais` respondem 200 do mesmo
origin. **Ajustar CORS não resolve nada** — falta fonte, não cabeçalho.

---

## 2. Os quatro endpoints e seus consumidores

| Endpoint | Router | `response_model` | Service | Consumidor no frontend | Comportamento na falha |
| --- | --- | --- | --- | --- | --- |
| `/brand-detail` | `performance.py:360` | `BrandDetailResponse` | `gold_service.get_brand_detail` (1662–1919) | `app/brand/[brand]/page.tsx:204` — **seção mensal TikTok** | seção indisponível; o resto da página vive de `/daily` (Neon, 200) |
| `/tempo-real` | `:355` | `TempoRealResponse` | `get_tempo_real` (1490–1594) | `app/tempo-real/page.tsx:177` | estado indisponível; o polling preserva o último dado válido (não há nenhum) |
| `/inteligencia` | `:412` | **nenhum** | `get_inteligencia` (2096–2314) | `app/inteligencia/page.tsx:135` | estado indisponível nomeado |
| `/operacoes` | `:417` | **nenhum** | `get_operacoes` (2315–2507) | `app/operacoes/page.tsx:145` | estado indisponível nomeado |

Nas quatro, o frontend **degrada honestamente**: nomeia a indisponibilidade, não
inventa número e não cai em mock fora do modo demonstração. Duas observações que
importam para a migração:

- `/inteligencia` e `/operacoes` **não têm `response_model`**. Não existe schema
  fixando o contrato — a compatibilidade depende do que o frontend lê, e isso precisa
  ser travado por teste **antes** de trocar a fonte (§19).
- `/brand-detail` é chamado **secundariamente** e com o **mês anterior**
  (`ref_month=2026-05` observado), o que explica por que a falha passa desapercebida
  numa inspeção superficial da página de Marca.

---

## 3. Matriz fonte → grão → chave → destino

Nove objetos do Data Mart sustentam os quatro endpoints. As colunas temporais reais
são `date`, `ref_date` e `date_brt`; `brand` é **texto** (a marca, não `loja_id`).

| # | Fonte (Data Mart) | Natureza | Grão real | Chave | Filtros no SQL | Endpoints | Destino proposto no Neon |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | `gold.tiktok_brand_daily` | série diária | dia × marca | `(date, brand)` | `brand`, `date BETWEEN` | brand-detail, operacoes | `marts.fact_tiktok_brand_content_daily` |
| 2 | `gold.tiktok_creator_daily` | série diária | dia × marca × criador | `(date, brand, creator)` | `brand`, `date BETWEEN` | brand-detail, operacoes | `marts.fact_tiktok_creator_daily` |
| 3 | `gold.tiktok_product_daily` | série diária | dia × produto | **`(date, product_id)`** | `brand`, `date >=` | brand-detail, inteligencia | **`marts.fact_tiktok_product_daily` — JÁ EXISTE** |
| 4 | `gold.v_channel_efficiency` | **view** | dia × marca | `(date, brand)` | `brand`, `date BETWEEN` | brand-detail | **decisão pendente** (§4.1) |
| 5 | `gold.tiktok_shop_hourly` | série **horária** | dia × hora × marca | `(date_brt, hour_brt, brand)` | `date_brt = CURRENT_DATE`; e os 7 dias anteriores | tempo-real | `marts.fact_tiktok_hourly` |
| 6 | `gold.ml_produto_ranking` | **snapshot** | produto (estado atual) | **`(brand, item_id)`** | `brand IN`, `product_status`, `pareto_bucket` | inteligencia | **`marts.fact_ml_produto_ranking` — JÁ EXISTE** |
| 7 | `gold.ml_cross_company_summary` | **snapshot** | marca | `(brand)` | `brand IN` | inteligencia | `marts.fact_ml_cross_company_summary` |
| 8 | `gold.ml_gestao_diaria` | série diária | dia × marca | `(ref_date, brand)` | `brand IN`, `ref_date >=` | operacoes | `marts.fact_ml_gestao_diaria` |
| 9 | `raw.tiktok_shop_orders` | eventos | pedido | `order_id` | só em `/debug/raw-tempo-real` | (debug) | **fora de escopo** |

### 3.1 O que já existe no Neon

| Fonte | Já existe equivalente? | Diagnóstico |
| --- | --- | --- |
| 1 `tiktok_brand_daily` | **parcialmente** | `marts.fact_marketplace_daily_performance` tem GMV/pedidos por dia × marca × canal, mas **não** as colunas de conteúdo e audiência (`total_views`, `active_videos`, `new_videos_posted`, demographics) — é exatamente isso que `brand-detail` consome. Registrado em §2 de `gold_vs_marts_matrix.md` |
| 2 `tiktok_creator_daily` | **não** | grão por criador sem equivalente |
| 3 `tiktok_product_daily` | **SIM — já existe** | `marts.fact_tiktok_product_daily`, criada pela migration `004_create_product_tables.py`, chave `UNIQUE (date, product_id)`, sincronizada por `pipelines/sync_produtos.py` e já consumida por `performance_service.py:2247` na página de Produtos. Ver §3.5 |
| 4 `v_channel_efficiency` | **não** | é view, e a definição **não é nossa** nem está versionada em repositório nosso |
| 5 `tiktok_shop_hourly` | **não** | o mart é diário; especificado no mesmo handoff §2 |
| 6 `ml_produto_ranking` | **SIM — já existe** | `marts.fact_ml_produto_ranking`, mesma migration `004`, chave `UNIQUE (brand, item_id)`, sincronizada por `sync_produtos.py` e já consumida por `performance_service.py:2064`. Ver §3.5 |
| 7 `ml_cross_company_summary` | **não** | lógica multi-company específica |
| 8 `ml_gestao_diaria` | **não** | existe `pipelines/transforms/ml_gestao_diaria.py`, mas ele alimenta a **gold**, não o mart |

**Correção da primeira versão deste blueprint.** Ela afirmava que as fontes 3 e 6 não
tinham equivalente no Neon. **Estava errado**: as duas tabelas existem, são
sincronizadas e já servem a página de Produtos. A reauditoria está em §3.5, e as
consequências percorrem §4, §18, §22, §23 e §25 — a contagem de tabelas novas caiu de
sete para **quatro**.

**Fonte de verdade:** nos nove casos, o Data Mart. Nenhuma destas tabelas é produzida
por este repositório — a transformação da `gold` **não está versionada aqui**
(constatação do G4 e do handoff §3) e nosso acesso é por réplica de leitura. Isso é
decisivo para o desenho: **não podemos recalcular, só copiar.**

### 3.2 Janela histórica, cadência e watermark

| Fonte | Janela necessária | Cadência de leitura pela API | Sincronização proposta | Watermark | Dedup por |
| --- | --- | --- | --- | --- | --- |
| 1 | ≥ 13 meses (mês corrente + comparativo + histórico) | mensal | diária, janela fechada | `date` | `(date, brand)` |
| 2 | ≥ 13 meses | mensal e 7 dias | diária, janela fechada | `date` | `(date, brand, creator)` |
| 3 | ≥ 13 meses | mensal e 30 dias | **já sincronizada**: incremental, últimos 7 dias + hoje | `date` | `(date, product_id)` |
| 4 | ≥ 13 meses | mensal | — (§4.1) | `date` | `(date, brand)` |
| 5 | dia corrente + 7 dias anteriores | **intraday** (a tela recarrega a cada 5 min) | **15–30 min**, janela de 2 dias | `(date_brt, hour_brt)` | `(date_brt, hour_brt, brand)` |
| 6 | estado atual | a cada carga da tela | **já sincronizada**: substituição integral | não se aplica | `(brand, item_id)` |
| 7 | estado atual | a cada carga da tela | diária, **substituição integral** | não se aplica | `(brand)` |
| 8 | 7 dias | 7 dias | diária, janela fechada | `ref_date` | `(ref_date, brand)` |

**Snapshots não têm watermark.** `ml_produto_ranking` e `ml_cross_company_summary`
são estado corrente, sem coluna temporal nas consultas — a única sincronização
correta é **substituição integral**, nunca upsert incremental. Se algum dia se
quiser histórico deles, é preciso acrescentar `snapshot_date` **no destino** e passar
a acumular: decisão de produto, não desta task.

**Late-arriving data é a regra, não a exceção.** O status de um pedido muda depois da
criação (handoff §4, item 3), então uma janela fechada de D-1 não basta: a
recomputação precisa de janela móvel (§11). Este é exatamente o defeito já conhecido
do Scheduler atual — lookback de 3 dias com buracos permanentes que `MAX(data)` não
detecta.

### 3.5 Reauditoria das duas fatos de produto que já existem

Comparação coluna a coluna entre o que os endpoints consomem da gold e o que as
tabelas do Neon oferecem. Read-only, sobre a migration `004` e o SQL dos services.

**`marts.fact_ml_produto_ranking`** — chave `UNIQUE (brand, item_id)`.
`get_inteligencia` consome: `brand`, `title`, `product_status`, `pareto_bucket`,
`revenue_velocity`, `ad_efficiency`, `gross_revenue`, `units_sold`,
`unique_buyers`, `cancel_rate_pct`, `ad_spend`, `ad_roas`, `ad_acos_pct`,
`days_advertised`, `revenue_share_pct`. (`n_products`, `avg_roas` e `gmv` são
apelidos de agregados, não colunas de origem.) **Todas as 15 existem na tabela do
Neon.**

**`marts.fact_tiktok_product_daily`** — chave `UNIQUE (date, product_id)`.

- `get_inteligencia` consome `brand`, `product_name`, `gmv`, `orders`,
  `pct_gmv_video`, `pct_gmv_live`, `pct_gmv_card`, `rating_avg`. **Todas
  existem.**
- `get_brand_detail` consome `product_id`, `product_name`, `gmv`, `orders` —
  presentes — **mais `active_videos` e `video_views`**, que **não existem** na
  tabela do Neon. São usadas para derivar `videos = SUM(active_videos)` e
  `gpm = SUM(gmv) / SUM(video_views) * 1000`.

**Classificação por endpoint:**

| Endpoint | Fonte | Classificação |
| --- | --- | --- |
| `/inteligencia` | `ml_produto_ranking` | **reutilizável sem mudança** |
| `/inteligencia` | `tiktok_product_daily` | **reutilizável sem mudança** |
| `/brand-detail` (seção de produtos) | `tiktok_product_daily` | **reutilizável após adicionar coluna**: `active_videos`, `video_views` |

**Nenhuma tabela duplicada será proposta.** As duas existentes são a única verdade
para esses produtos; o que falta é **acrescentar duas colunas** a uma delas, por
migration aditiva, e estender o `SELECT` de `sync_produtos.py` para trazê-las.

**Cadência e estratégia atuais de `pipelines/sync_produtos.py`** (já em produção,
não propostas):

| Fonte | Estratégia | Chave do upsert |
| --- | --- | --- |
| `gold.ml_produto_ranking` | **substituição integral** (snapshot sem dimensão temporal) | `ON CONFLICT (brand, item_id)` |
| `gold.tiktok_product_daily` | **incremental por `date`**, últimos 7 dias + hoje | `ON CONFLICT (date, product_id)` |

Tem ainda uma guarda anti-truncamento: abaixo de um percentual do total anterior no
Neon, uma fonte que fez substituição integral é rejeitada. O `health_check.py`
acompanha `MAX(date)` de `fact_tiktok_product_daily` — **e é exatamente o critério
que §15 recomenda substituir por cobertura**, uma dívida do que já existe, não desta
proposta.

### 3.3 Regras de marketplace e status a preservar

- **Escopo de marcas.** `BRANDS_IN_SCOPE` (5 marcas) e `ML_BRANDS` são filtros
  aplicados no SQL de origem. A cópia deve preservá-los, ou copiar o superconjunto e
  filtrar na leitura — **nunca** ampliar o escopo silenciosamente.
- **`product_status` e `pareto_bucket`** (`ml_produto_ranking`) são categorias vindas
  da gold, consumidas literalmente pela tela de Inteligência. São dados, não regra
  nossa: copiar sem reinterpretar.
- **`brand` é texto**, não `loja_id`. Não converter na cópia: a chave que os
  endpoints e o frontend usam é a marca em texto.
- **Fuso.** `tiktok_shop_hourly` já vem em BRT (`date_brt`/`hour_brt`). A cópia
  **não deve** reconverter; o handoff §2 alerta explicitamente para não assumir que
  `created_at` esteja em BRT.
- **Allowlist de status do TikTok** (`COMPLETED/DELIVERED/IN_TRANSIT`) vale para o
  cálculo de GMV a partir da Raw. As tabelas 1–5 já chegam calculadas pela gold, então
  a allowlist **não** se reaplica na cópia.
- **TikTok sem cancelamento/devolução** e **Regiões medindo menos que Canais** são
  limitações de dado já documentadas (DQ1/DQ2). A cópia não as corrige nem as piora.

### 3.4 Dependência semântica separada: GMV TikTok com frete

`docs/tiktok_gmv_com_frete_decisao.md` registra a decisão de incluir frete no GMV do
TikTok, com a **escolha da coluna ainda pendente** (`total_amount` contra
`sub_total + shipping_fee`, com R$ 36.138,24 de resíduo não identificado).

**Isso não entra nesta arquitetura.** Produção continua na regra vigente
(`sub_total`) até decisão explícita. A camada de serving **copia** o que a gold
entrega; se a definição mudar, muda na origem e/ou no conector, e a cópia acompanha.
Nenhuma tabela proposta aqui embute a decisão de frete, e nenhum campo novo de frete
é criado por esta frente.

---

## 4. Tabelas propostas no Neon

Schema `marts`, mesmo padrão das fatos existentes (`fact_marketplace_daily_performance`,
`fact_marketplace_region_daily`), criadas por **migration Alembic** — a numeração
segue de `005_create_fact_marketplace_region_daily.py`.

**Quatro tabelas novas**, não sete: as fontes 3 e 6 já têm destino (§3.5), a 4 está
pendente de decisão (§4.1) e a 5 pertence à fase de Tempo Real (§25.3).

| Tabela | Estado | PK | Índices sugeridos | Origem |
| --- | --- | --- | --- | --- |
| `marts.fact_ml_gestao_diaria` | **nova** (S1) | `(ref_date, brand)` | `(brand, ref_date)` | fonte 8 |
| `marts.fact_tiktok_brand_content_daily` | **nova** (S2) | `(date, brand)` | `(brand, date)` | fonte 1 |
| `marts.fact_tiktok_creator_daily` | **nova** (S2) | `(date, brand, creator)` | `(brand, date)`, `(date)` | fonte 2 |
| `marts.fact_ml_cross_company_summary` | **nova** (S3) | `(brand)` | — | fonte 7 |
| `marts.fact_tiktok_product_daily` | **existe** — falta migration **aditiva** de `active_videos`, `video_views` | `(date, product_id)` | já criados | fonte 3 |
| `marts.fact_ml_produto_ranking` | **existe** — nada a fazer | `(brand, item_id)` | já criados | fonte 6 |
| `marts.fact_tiktok_hourly` | **fase de Tempo Real**, fora dos três gates | `(date_brt, hour_brt, brand)` | `(brand, date_brt)` | fonte 5 |

Cada tabela recebe `CHECK` contra os defeitos já vividos neste projeto: coluna
numérica com `CHECK (col <> 'NaN')` explícito, porque **`'NaN'::numeric >= 0` é
TRUE** no Postgres e passaria por um `CHECK (col >= 0)` sozinho.

### 4.2 Contrato de colunas — lista explícita, nunca espelho da origem

**A camada de serving não copia a Gold inteira.** Copiar tudo importa colunas que
ninguém consome, arrasta atributos e identificadores sem uso, e transforma qualquer
mudança na origem em mudança silenciosa no nosso destino.

Regras, válidas para toda tabela desta frente:

1. **Lista explícita e versionada de colunas**, declarada na migration e no módulo de
   sync. **Zero `SELECT *`**, na extração e na publicação.
2. Copiar **somente**: as colunas da chave, os campos efetivamente consumidos pelos
   quatro endpoints, e os campos de auditoria técnica.
3. Quando uma fonte atende **mais de um endpoint**, a lista é a **união** das colunas
   consumidas — e essa união é declarada, não inferida em tempo de execução.
4. **Tipos explícitos** na migration. Nada de inferência a partir da origem.
5. Coluna nova exige **alteração consciente de contrato**: migration aditiva +
   atualização da lista no sync + revisão de quem passa a consumi-la. Não existe
   "veio de graça".
6. **Preservar os nomes da origem** quando isso evita mudar o SQL do service — a
   migração deve trocar `gold.` por `marts.` e nada mais, sempre que possível.
7. **Não copiar identificadores nem atributos que nenhum endpoint lê** (nomes
   internos, chaves técnicas da origem, campos de PII).

#### Contrato por tabela

| Tabela | Chave | Colunas mínimas / grupos consumidos | Consumidores | Auditoria |
| --- | --- | --- | --- | --- |
| `marts.fact_ml_gestao_diaria` | `(ref_date, brand)` | métricas de gestão diária lidas por `get_operacoes` (velocidade de mídia e alertas) — **lista a fechar no S1 lendo o `SELECT` do service** | `/operacoes` | `synced_at`, `source_run_id` |
| `marts.fact_tiktok_brand_content_daily` | `(date, brand)` | união de `/brand-detail` e `/operacoes`: conteúdo e audiência (`total_views`, `active_videos`, `new_videos_posted`, demographics consumidas) — **sem** GMV/pedidos, que já vivem em `fact_marketplace_daily_performance` | `/brand-detail`, `/operacoes` | `synced_at`, `source_run_id` |
| `marts.fact_tiktok_creator_daily` | `(date, brand, creator)` | união de `/brand-detail` (top criadores do mês) e `/operacoes` (tabela de criadores) | `/brand-detail`, `/operacoes` | `synced_at`, `source_run_id` |
| `marts.fact_ml_cross_company_summary` | `(brand)` | somente os campos que `get_inteligencia` lê do resumo por marca | `/inteligencia` | `synced_at`, `source_run_id` |
| `marts.fact_tiktok_product_daily` **(existe)** | `(date, product_id)` | já cobre `/inteligencia`; **acrescentar `active_videos` e `video_views`** para `/brand-detail` (§3.5) | `/produtos`, `/inteligencia`, `/brand-detail` | `ingested_at` (já existe) |
| `marts.fact_ml_produto_ranking` **(existe)** | `(brand, item_id)` | nada a acrescentar | `/produtos`, `/inteligencia` | `ingested_at`, `refreshed_at` (já existem) |

**Evolução de schema.** Toda alteração é **aditiva** e passa por migration nomeada:
nunca renomear, nunca remover, nunca reordenar. Remoção de coluna só depois de provar
que nenhum endpoint a lê — e isso é gate próprio, não efeito colateral. As duas
tabelas que já existem seguem servindo a página de Produtos durante toda a frente:
**qualquer mudança nelas é aditiva por obrigação**, sob pena de quebrar um consumidor
em produção.

### 4.1 `v_channel_efficiency` — decisão pendente

É a única fonte que **não é tabela**. Três opções, nenhuma decidida:

1. **Copiar o resultado da view** como fato diário (`marts.fact_channel_efficiency_daily`).
   Simples, mas congela uma definição que não controlamos: se a view mudar na origem,
   a cópia divergirá em silêncio.
2. **Reimplementar a expressão** no Neon a partir das fatos já copiadas. Só é viável
   se a definição da view for legível e derivável das fontes 1–3 — **não verificado**,
   porque exige `pg_get_viewdef` com VPN.
3. **Não copiar** e deixar a seção correspondente de `brand-detail` declaradamente
   indisponível, como hoje.

**Recomendação:** decidir só depois de ler a definição da view com VPN (Gate S1,
critério de aceite). Até então, a opção 3 é a honesta, e é o estado atual — não
piora nada.

---

## 5. Copiar fatos, criar marts de serving ou cachear por endpoint?

**Decisão: copiar fatos Gold estáveis, no grão da origem.** Um fato por fonte, sem
transformação.

Por quê:

- **Reuso comprovado pela matriz.** Três das oito tabelas servem **dois** endpoints
  cada (`tiktok_brand_daily` → brand-detail + operacoes; `tiktok_creator_daily` →
  brand-detail + operacoes; `tiktok_product_daily` → brand-detail + inteligencia). Um
  cache por endpoint duplicaria essas cópias e criaria duas verdades para o mesmo
  número.
- **A agregação já é barata.** Os `GROUP BY` dos quatro endpoints são por criador,
  produto ou status, sobre janelas de 7–30 dias em cinco marcas. Não é o volume que
  justifica materializar recorte — é a **conectividade**.
- **Precedente interno.** `sync_region_daily.py` já copia `gold.marketplace_region_daily`
  → `marts.fact_marketplace_region_daily` no grão da origem, com validação por
  agregados. Reaproveitar esse padrão é mais seguro que inventar outro.
- **Contra o cache JSON por endpoint:** invalidaria a possibilidade de novos recortes
  (drill-downs, filtros) sem novo job, acoplaria o schema de armazenamento ao schema
  de resposta da API, e tornaria qualquer mudança de UI uma mudança de pipeline. Só se
  justificaria se houvesse gargalo de latência comprovado — e não há: com fonte
  acessível as consultas rodam em ~4s no total, e no Neon serão mais rápidas.

**Marts de serving derivados (agregações próprias) ficam fora por ora.** Só valem se
uma consulta específica se provar lenta no Neon, o que é medição do Gate S2, não
suposição de agora.

---

## 6. Fluxo recomendado

```
Data Mart (RDS, VPN)              ← somente leitura
   │  extração incremental por janela móvel (ou integral, nos snapshots)
   ▼
EXECUTOR com acesso ao RDS      ← condição obrigatória, ainda NÃO provada (§24.1)
   │
   ▼
Neon: staging TEMP na mesma transação
   │  validações: contagem, somas, duplicidade na chave, nulos, NaN
   ▼
publicação atômica  (upsert por janela  |  TRUNCATE+INSERT nos snapshots)
   │
   ▼
marts.fact_*  →  FastAPI/Render (engine principal)  →  frontend
```

Duas propriedades vêm do desenho, não de disciplina operacional:

- **O Render nunca fala com o Data Mart.** Depois da migração, `_uses_datamart()`
  deixa de ser acionado pelos quatro endpoints, porque o SQL passa a dizer `marts.`.
- **O executor responsável pela extração é o único componente que precisa alcançar o
  Data Mart; se esse executor for o worker Airflow, sua conectividade precisa ser
  provada.** Enquanto não for, o executor pode ser uma máquina em rede com VPN
  rodando o mesmo módulo — o desenho não muda, só quem o dispara (§24.1).

---

## 7. Primeira carga histórica

Padrão de `sync_region_daily.py`, adaptado:

1. Ler **todas** as linhas da fonte no recorte histórico (≥ 13 meses), conexão
   somente leitura.
2. Abrir **uma única transação** no Neon: `LOCK TABLE ... IN ACCESS EXCLUSIVE MODE`,
   criar staging `TEMP ... ON COMMIT DROP`, inserir.
3. Validar staging contra a fonte por **agregados calculados em Python** — contagem e
   somas de todas as colunas numéricas — porque a comparação `EXCEPT` cross-database
   é impossível: fonte e destino são servidores diferentes.
4. Se a tabela real já tiver linhas, criar **backup** `marts.<tabela>_backup_<tag>`
   antes de tocá-la.
5. `TRUNCATE` + `INSERT` a partir da staging validada, na mesma transação.
6. `EXCEPT` real **dentro do Neon** (staging × real) após o insert, para provar
   igualdade linha a linha.

Volume estimado, para dimensionar: 5 marcas × ~400 dias = ~2 mil linhas na fonte 1;
a fonte 3 (produto) é a maior — a Raw tem 2,8 mi de itens, mas o grão agregado
dia × marca × produto deve ficar em ordem de 10⁵–10⁶ linhas. **Medir antes de
carregar** é critério de aceite do Gate S1.

## 8. Estratégia incremental

- **Séries diárias (1, 2, 3, 8):** janela móvel de **D-N a D-1**, com `N ≥ 7` para
  absorver late-arriving data, e `MERGE`/upsert por chave. Nunca `MAX(date)` como
  watermark — é justamente o que deixou buracos permanentes no Scheduler atual.
- **Horária (5):** janela de **2 dias** (`CURRENT_DATE - 1` e `CURRENT_DATE`), a cada
  15–30 min, upsert por `(date_brt, hour_brt, brand)`.
- **Snapshots (6, 7):** **substituição integral** diária. Não há incremento possível.

## 9. Sobreposição e deduplicação

A janela móvel **sobrepõe** de propósito. A deduplicação é a chave primária: `INSERT
... ON CONFLICT (chave) DO UPDATE`. Reprocessar o mesmo dia N vezes converge para o
mesmo estado — é o que torna o job idempotente. As chaves de dedup por fonte estão em
§3.2.

Risco a tratar explicitamente: **linha que desaparece da origem**. Upsert não apaga.
Para as séries diárias, a janela deve ser publicada com `DELETE` do intervalo +
`INSERT`, dentro da transação — não `ON CONFLICT` puro — para que a cópia reflita
remoções. Nos snapshots, `TRUNCATE`+`INSERT` já resolve.

## 10. Transações e publicação atômica

Uma transação por tabela e por janela. Dentro dela: lock, staging, validação, delete
do intervalo, insert, verificação. **Se qualquer validação falhar, `ROLLBACK`** e a
tabela real permanece exatamente como estava — nenhum estado intermediário fica
visível para a API. `ON COMMIT DROP` na staging garante limpeza mesmo em rollback.

## 11. Watermark e late-arriving data

O watermark é **por janela, não por linha**: o job registra `(tabela, janela_inicio,
janela_fim, executado_em)` numa tabela de auditoria e recomputa a janela inteira. A
consequência é que o health check **não pode ser `MAX(data)`** — precisa ser
**cobertura**: para cada dia do intervalo esperado, existe linha? Foi essa distinção
que o achado de 05/08/2026 sobre o Scheduler tornou obrigatória.

## 12. Retries seguros

Como cada execução recomputa a janela inteira dentro de uma transação, **retry é
seguro por construção**: repetir produz o mesmo estado. Requisitos: `retries` com
backoff no operator, **advisory lock** no Neon por tabela para impedir duas execuções
concorrentes da mesma janela (padrão já usado em `run_shopee_gold_batch.py`), e
`statement_timeout` explícito para o job não ficar pendurado.

## 13. Rollback

Três níveis: (a) `ROLLBACK` da transação, automático em qualquer falha de validação;
(b) **backup por tag** antes de qualquer carga integral, como em `sync_region_daily.py`;
(c) restauração a partir do backup, como procedimento manual documentado em runbook.
Não propor rollback automático de dado publicado — decisão humana.

## 14. Auditoria de execução

Reutilizar o padrão existente `source_sync_run` (já usado no backfill de ML/TikTok):
por execução, registrar tabela, janela, linhas lidas, linhas publicadas, agregados
da fonte e do destino, duração, status e mensagem de erro **sanitizada** (sem DSN —
`sync_region_daily.py` já tem `_sanitize_error_message` para isso).

## 15. Frescor e health check

- **Por cobertura**, não por `MAX`: para cada `(tabela, dia)` do intervalo esperado,
  existe linha? Buraco no meio precisa aparecer.
- **Frescor exposto na API**: as tabelas novas carregam `synced_at`, e os endpoints
  passam a devolvê-lo — o frontend já sabe exibir `refreshed_at` e a faixa de
  confiança da Gerencial já tem lugar para isso.
- `/health-datasource` deve passar a reportar também a idade da última sincronização
  por tabela, e não só `db_connected`.
- **Tempo Real precisa de critério próprio:** a tela se diz "ao vivo". Com sync de
  15–30 min ela **não é** ao vivo, e isso precisa ser declarado na interface — o
  rótulo atual passaria a ser impreciso. Decisão de produto a registrar no Gate S2.

## 16. Alertas

Falha de execução, janela com cobertura incompleta, e divergência de agregados
fonte × destino. Canal e política de escalonamento seguem a convenção do repositório
Airflow — **não verificável nesta task** (§24). Sem alerta, um sync que para de rodar
é indistinguível de um sync que roda: as telas simplesmente ficam velhas em silêncio.

## 17. Segurança e secrets

- DSN do Data Mart e do Neon **somente** como connection/secret do Airflow. Nunca em
  código, log, mensagem de erro ou artefato.
- Credencial do Data Mart **read-only** (já existe o papel `datamart-gogroup-reader`).
- No Neon, o usuário do job precisa de escrita **apenas** no schema `marts` e nas
  tabelas desta frente.
- Mensagens de erro sanitizadas, com o padrão já implementado.
- **Nenhuma credencial nova é criada por esta task.**

## 18. Custos e volume

O incremento diário é pequeno (5 marcas). **A fonte 3 (produto) sai da conta de
custo desta frente**: já está carregada e sincronizada no Neon (§3.5), e o que resta é
uma migration aditiva de duas colunas. O peso remanescente está na **cadência intraday
da fonte 5**, que pertence à fase de Tempo Real. Para as quatro tabelas novas, as
medições de contagem de linhas e tamanho em disco continuam pré-requisito do gate que
cria cada uma — sem esses números, estimativa de custo é chute.

## 19. Contrato de compatibilidade da API

A migração é **fonte, não contrato**: o JSON de resposta deve permanecer idêntico.

- `/brand-detail` e `/tempo-real` têm `response_model` — o Pydantic já é a guarda.
- `/inteligencia` e `/operacoes` **não têm**. Antes de trocar a fonte, é necessário
  **congelar o formato atual em teste** (snapshot do dicionário retornado, com dados
  fixos), senão a migração pode mudar o payload sem ninguém perceber.
- Regra dura: **nenhum campo renomeado, removido ou reordenado** na migração. Campo
  novo (ex.: `synced_at`) é aditivo e opcional.
- Nenhum recálculo: se a gold entrega um número, a cópia entrega o mesmo número. Toda
  divergência é bug, não melhoria.

## 20. Migração endpoint por endpoint

Um endpoint por vez, com a fonte antiga ainda disponível para comparação sob VPN:

1. Criar tabela + sync + backfill.
2. Comparar, com VPN, a resposta antiga (gold) e a nova (marts) para o mesmo
   parâmetro — igualdade campo a campo.
3. Trocar o SQL do service de `gold.` para `marts.`.
4. Publicar o backend.
5. Smoke do endpoint em produção.
6. Só então o próximo.

Nenhum "big bang". A troca de `gold.` para `marts.` desliga o `_uses_datamart()`
para aquele endpoint — é a linha que remove a dependência de VPN.

## 21. Critérios de aceite

1. Os quatro endpoints respondem **200 em produção**, sem VPN.
2. Resposta **idêntica** à da fonte gold para os mesmos parâmetros, verificado com VPN.
3. Sync **idempotente**: duas execuções seguidas produzem o mesmo estado.
4. Cobertura **sem buracos** no intervalo esperado, verificada por dia.
5. Falha de validação **não publica** nada (rollback comprovado por teste).
6. Nenhum DSN em log, erro ou artefato.
7. `synced_at` exposto e visível na interface.
8. Zero mudança no payload dos endpoints (testes de contrato).
9. Alerta dispara quando o sync falha ou a cobertura fica incompleta.

## 22. Riscos e decisões pendentes

| # | Risco / decisão | Severidade | Encaminhamento |
| --- | --- | --- | --- |
| 1 | **O executor pode não alcançar o RDS.** Não presumido, não provado | **Bloqueador da integração Airflow** | Dois resultados distintos (§24.1): o **piloto** pode rodar de máquina com VPN e provar schema/carga/idempotência; a **prova operacional do Airflow** exige `SELECT 1` de dentro do worker real. Sem ela, nenhuma afirmação de conectividade do Airflow pode ser feita |
| 2 | **Repositório Airflow existe** (informado pelo proprietário) mas **não foi localizado nem está visível** com as credenciais desta sessão | **Bloqueador da integração** | §24: falta nome/URL, modelo de hospedagem e rede |
| 3 | Definição de `v_channel_efficiency` desconhecida | Média | §4.1, decidir com VPN |
| 4 | ~~Volume da fonte 3 não medido~~ — **resolvido**: a tabela já existe e está sincronizada | — | medir apenas as **quatro tabelas novas**, no gate que cria cada uma |
| 5 | Cadência de Tempo Real (15–30 min) contradiz o rótulo "ao vivo" | Média | decisão de produto no Gate S2 |
| 6 | Snapshots sem histórico: 6 e 7 perdem o passado a cada carga | Média | aceitar por ora; `snapshot_date` é decisão de produto. Vale notar que a 6 **já se comporta assim em produção** hoje |
| 7 | Linha removida na origem não é apagada por upsert puro | Média | publicação por `DELETE` do intervalo + `INSERT` (§9) |
| 8 | `/inteligencia` e `/operacoes` sem `response_model` | Média | congelar o formato em teste antes de migrar (§19) |
| 9 | GMV TikTok com frete pode mudar a definição na origem | Baixa para esta frente | frente **separada**; a cópia acompanha a origem (§3.4) |
| 10 | Concorrência entre o Scheduler atual e o Airflow durante a transição | Média | advisory lock por tabela + desligar o job antigo só depois do novo provado |

---

## 23. Primeira fatia vertical recomendada

**`/operacoes`.** Não é a única defensável, mas é a que mais prova com menos risco:

- Suas três fontes (`ml_gestao_diaria`, `tiktok_brand_daily`, `tiktok_creator_daily`)
  são **todas séries diárias com watermark natural** — exatamente o padrão que
  `sync_region_daily.py` já resolveu neste repositório.
- **Duas das três são reaproveitadas** por `/brand-detail`, então o segundo endpoint
  fica muito mais barato: sobra `tiktok_product_daily` e a decisão da view.
- **Nenhuma view a materializar** e nenhum snapshot: o caminho incremental completo é
  exercitado, que é a parte difícil.
- **Cadência diária**, compatível com a janela do `full_daily` já agendado.
- Entrega **valor real**: a tela de Operações volta a funcionar.

**Ordem recomendada:** `/operacoes` → `/brand-detail` → `/inteligencia` →
**`/tempo-real` por último**. Tempo Real é deliberadamente o fim da fila: grão
horário, cadência intraday, janela de 2 dias e um rótulo de "ao vivo" que precisa de
decisão de produto. Misturá-lo na primeira fatia acopla o problema fácil ao difícil.

**Não publicar as quatro de uma vez.** O modo de falha de um big bang aqui é o pior
possível: quatro telas trocando de fonte ao mesmo tempo, sem baseline de comparação,
com a fonte antiga inacessível para conferência a partir de produção.

---

## 24. Auditoria do repositório Airflow — EXISTE, MAS NÃO VISÍVEL NESTA SESSÃO

**Fato correto:** o proprietário informou que **existe um repositório Airflow na
organização**. Ele **não foi localizado nem está visível** com as credenciais desta
sessão. Plataforma, URL, modelo de hospedagem e topologia de rede **ainda não foram
informados**, e **nenhuma DAG foi inspecionada, configurada ou executada**.

Nada foi instalado, nenhuma credencial foi criada, nenhum clone foi feito.

O que foi tentado, read-only, pelo acesso GitHub já configurado:

| Busca | Resultado |
| --- | --- |
| `org:b2b-gogroup airflow` | 0 repositórios |
| `org:b2b-gogroup` (todos) | 1 repositório: `b2b-agent` — não é Airflow |
| `user:Switerz airflow` | 0 repositórios |
| `user:Switerz` (todos) | 8 repositórios, nenhum de Airflow (`mktplace`, `markov`, `finance`, `gotrends`, `gotrends2`, `gti`, `formstransp`, perfil) |
| `org:gocase` | 0 repositórios visíveis |
| busca de código (`DAG airflow`) | **falhou**: `Authentication Failed: Requires authentication` — o token disponível não faz code search |

**Informação que falta, exatamente:**

1. **nome e URL** do repositório Airflow (org e repo) — ele existe; o que falta é
   sabermos onde;
2. **acesso de leitura** para o token/credencial já usado por este ambiente — o token
   atual enxerga apenas repositórios públicos de `Switerz` e `b2b-gogroup`, e não faz
   code search;
3. se o Airflow é **auto-hospedado ou gerenciado** (MWAA, Composer, Astronomer), o que
   muda profundamente connections, deploy e rede.

Portanto **nada foi possível inspecionar** sobre: estrutura de DAGs, padrão de
connections/secrets, operators/hooks disponíveis, ambientes e forma de deploy,
convenções de retries/pools/timeouts/SLAs/alertas, acesso esperado ao Data Mart,
acesso esperado ao Neon, testes e CI, política de backfill, e exemplos de cargas
Postgres → Postgres.

**Isto não bloqueia o blueprint** — o desenho de §3 a §23 é independente do
orquestrador. Bloqueia a **integração concreta**, que passa a ser dependência
declarada do Gate S1.

### 24.1 Dois resultados distintos, que não se substituem

**Não se presume que o worker do Airflow alcance o Data Mart.** O RDS exige VPN, e o
Render — outro serviço gerenciado — não a tem. A confusão a evitar é tratar "o sync
rodou" como "o Airflow funciona". São duas provas separadas:

**1. Piloto técnico do módulo** — pode ser feito já:

- implementado e testado **neste** repositório;
- executado **manualmente**, de uma máquina com VPN;
- prova **schema, carga, idempotência e reconciliação** fonte × destino;
- **não prova nada sobre o Airflow**: nem conectividade, nem secrets, nem agendamento.

**2. Prova operacional do Airflow** — só de dentro do ambiente real:

- executada **de dentro do worker/executor** do Airflow;
- `SELECT 1` contra o **Data Mart**;
- acesso de escrita ao **Neon**;
- confirmação de que **secrets/connections** resolvem;
- **obrigatória** antes de declarar a integração Airflow pronta.

**Consequências, se o repositório/worker continuar inacessível:**

- o Gate S1 pode chegar no máximo a **`PARTIAL — PILOTO VALIDADO`**;
- **não** pode ser marcado como concluído;
- o Gate S2 **não** pode ativar DAG nem agendamento — a migração do endpoint fica
  dependente de execução manual documentada, e isso precisa estar declarado na tela
  (frescor) e no runbook;
- **nenhuma alegação de conectividade do Airflow pode ser feita** em documento,
  relatório ou status.

Se o worker não alcançar o RDS, a alternativa é um executor em rede com VPN (papel que
hoje o Windows Task Scheduler cumpre) publicando no Neon, com o Airflow apenas
orquestrando e observando — o desenho de §3 a §23 não muda.

### 24.2 O que este repositório já oferece como precedente

Independente do Airflow, o padrão de sync **já existe e está auditado** aqui:

- `pipelines/sync_region_daily.py` — Data Mart → Neon de `gold.marketplace_region_daily`
  para `marts.fact_marketplace_region_daily`: leitura integral da fonte, transação
  única no Neon com `LOCK TABLE ... ACCESS EXCLUSIVE`, staging `TEMP ... ON COMMIT
  DROP`, validação por agregados calculados em Python (contagem + somas), checagem de
  duplicidade na chave e de nulos, backup por tag antes de tocar a tabela real,
  `TRUNCATE`+`INSERT`, e `EXCEPT` staging × real dentro do Neon depois do insert.
  Tem também `_sanitize_error_message` para não vazar DSN.
- `pipelines/sync_produtos.py` — mesmo espírito, para produtos.
- `pipelines/ops/` — `health_check.py`, `orchestrate.py`, `preflight.py`,
  `schedule_plan.py`, `sync_region_if_needed.py`: preflight de privilégios, plano de
  agendamento e health check já implementados.
- `apps/api/alembic/versions/` — cinco migrations, a última justamente
  `005_create_fact_marketplace_region_daily.py`: o molde do DDL das tabelas novas.

**Consequência prática:** a lógica de sync pode ser escrita e testada **neste
repositório**, como módulo importável, e a DAG do Airflow ficar sendo apenas o
agendador que a invoca. Isso reduz o acoplamento ao orquestrador desconhecido e
permite avançar no Gate S1 mesmo antes de resolver o acesso ao repositório Airflow.

---

## 25. Roadmap — três gates

Nenhum deles foi executado. Os três são sequenciais, e o S1 é pré-requisito duro.

### Gate S1 — provar acesso e publicar a primeira tabela

> **Executado em 11/08/2026 — `PARTIAL — PILOTO VALIDADO`.** Registro completo com
> números, fingerprints e limites da prova em [§26](#26-execução-do-gate-s1--registro-do-piloto-11082026).

**Objetivo:** publicar **uma** tabela nova ponta a ponta e provar o **piloto técnico**
(§24.1, resultado 1), sem trocar nenhum endpoint. A **prova operacional do Airflow**
(resultado 2) é objetivo do mesmo gate **somente se** o repositório/worker estiver
acessível; se não estiver, o gate encerra como **`PARTIAL — PILOTO VALIDADO`**.

| Item | Conteúdo |
| --- | --- |
| **Repositório** | este (`mktplace`) para o módulo de sync e a migration; repositório Airflow **apenas** para o teste de conectividade, se localizado |
| **Arquivos prováveis** | `apps/api/alembic/versions/006_create_fact_ml_gestao_diaria.py`; `pipelines/sync_ml_gestao_diaria.py` (moldado em `sync_region_daily.py`); `pipelines/tests/` |
| **Escrita autorizada** | migration DDL no Neon (`marts`), e escrita de dados **somente** na tabela nova. Nenhuma tabela existente é tocada |
| **Dependências** | acesso VPN ao Data Mart **para o executor do piloto** (máquina local serve); para a prova operacional, nome/URL/acesso do repositório Airflow e do worker |
| **Testes** | idempotência (duas execuções → mesmo estado); rollback em validação falha; dedup na chave; cobertura sem buracos; nenhum DSN em log; agregados fonte × destino idênticos |
| **Aceite (piloto)** | tabela criada e carregada; agregados fonte × destino batendo; segunda execução sem diferença; rollback comprovado; contagem de linhas e tamanho em disco **medidos e registrados** |
| **Aceite (Airflow)** | `SELECT 1` no Data Mart **de dentro do worker**, escrita no Neon e secrets resolvendo. **Sem isso, o gate é `PARTIAL`** |
| **Stop-loss** | se **nem o piloto** alcançar o RDS (máquina com VPN inclusive), **parar** e replanejar a arquitetura — não tentar contorno por túnel improvisado. Se só o worker não alcançar, encerrar `PARTIAL` e reportar, sem afirmar conectividade do Airflow |

### Gate S2 — migrar `/operacoes` (primeira fatia vertical completa)

**Objetivo:** completar as três tabelas de `/operacoes` e trocar a fonte do endpoint,
com comparação contra a gold antes da troca.

| Item | Conteúdo |
| --- | --- |
| **Repositório** | este, para tabelas, sync, service e testes; repositório Airflow para a DAG diária |
| **Arquivos prováveis** | migrations `007`/`008` (`fact_tiktok_brand_content_daily`, `fact_tiktok_creator_daily`); syncs correspondentes; `apps/api/app/services/gold_service.py` (`get_operacoes`: `gold.` → `marts.`); teste de contrato congelando o payload atual |
| **Escrita autorizada** | DDL e dados nas tabelas novas; **publicação do backend no Render** ao final |
| **Dependências** | Gate S1 com piloto validado; backfill histórico executado. **DAG/agendamento somente se o worker estiver provado** (§24.1); caso contrário, execução manual documentada em runbook e frescor declarado na tela |
| **Testes** | contrato: payload de `/operacoes` **idêntico** ao atual (snapshot com dados fixos); comparação gold × marts com VPN; suíte da API verde |
| **Aceite** | `/operacoes` em **200 em produção sem VPN**, com payload idêntico ao contrato congelado; `synced_at` exposto; cobertura sem buracos; alerta configurado **se** houver Airflow provado — senão, o runbook declara a operação manual |
| **Stop-loss** | qualquer divergência de payload ou de número **para** a troca. Não migrar "quase igual" |

### Gate S3 — migrar `/brand-detail` e `/inteligencia`

**Objetivo:** fechar os dois endpoints restantes de cadência diária **reutilizando as
duas fatos de produto que já existem** e criando apenas o destino realmente ausente.
`/tempo-real` **não** entra aqui — §25.3.

| Item | Conteúdo |
| --- | --- |
| **Repositório** | este; repositório Airflow para as DAGs, **se** o worker estiver provado |
| **Reuso obrigatório** | `marts.fact_ml_produto_ranking` (**nada a fazer**) e `marts.fact_tiktok_product_daily` (**migration aditiva** de `active_videos` e `video_views`, mais o `SELECT` de `sync_produtos.py` estendido). **Nenhuma tabela nova de produto**, nenhuma segunda verdade |
| **Única tabela nova** | `marts.fact_ml_cross_company_summary` (snapshot por marca, substituição integral) |
| **Arquivos prováveis** | migration aditiva das duas colunas; migration de `fact_ml_cross_company_summary`; `pipelines/sync_produtos.py` (lista de colunas); sync novo do cross-company; `get_brand_detail` e `get_inteligencia` de `gold.` → `marts.` |
| **Escrita autorizada** | DDL aditivo em `fact_tiktok_product_daily`; DDL e dados em `fact_ml_cross_company_summary`; recarga das duas colunas novas na fato existente; publicação do backend |
| **Dependências** | Gate S2 aprovado; **decisão sobre `v_channel_efficiency`** (§4.1) com a definição da view em mãos |
| **Testes** | contrato dos dois endpoints (o de `/inteligencia` **precisa ser criado**, pois não há `response_model`); **regressão da página de Produtos**, que consome as duas fatos existentes e não pode quebrar; substituição integral idempotente; comparação com VPN |
| **Aceite** | os dois endpoints em 200 em produção sem VPN, payload idêntico; `/produtos` **sem regressão**; a seção de `brand-detail` que depende da view resolvida **ou declaradamente indisponível**, nunca aproximada |
| **Stop-loss** | se a definição da view não puder ser lida, **não** reimplementar por engenharia reversa: manter a seção indisponível e reportar. Qualquer regressão em `/produtos` para o gate |

### 25.1 Separação por natureza de mudança

| Natureza | Onde | Gates |
| --- | --- | --- |
| **Alterações neste repositório** | módulos de sync em `pipelines/`, migrations em `apps/api/alembic/versions/`, SQL dos services em `gold_service.py`, testes | S1, S2, S3 |
| **Alterações no repositório Airflow** | DAGs, connections, pools, alertas | S1 (só teste de conectividade), S2, S3 |
| **SQL/migrações no Neon** | `CREATE TABLE` em `marts` + índices + `CHECK` | S1, S2, S3 |
| **Publicação do backend Render** | somente quando o SQL de um service muda de `gold.` para `marts.` | S2, S3 |
| **Backfill inicial** | carga histórica por tabela, com backup por tag | S1 (uma tabela), S2, S3 |

### 25.2 O que fica fora dos três gates

A decisão do **GMV TikTok com frete** (frente separada, §3.4) e as dívidas de
acessibilidade dos filtros. Também fica fora qualquer mart de serving derivado: só se
uma consulta se provar lenta no Neon, medido, não suposto.

### 25.3 `/tempo-real` — próxima fase, não resolvido

Os três gates **não comportam** `/tempo-real`, e a classificação honesta é
**próxima fase**, não "resolvido". Razões:

- grão **horário** (`date_brt`, `hour_brt`), o único fora do padrão diário;
- cadência **intraday**: a tela recarrega a cada 5 min, e um sync de 15–30 min exige
  agendamento próprio, não a janela do `full_daily`;
- uma **decisão de produto pendente**: com sync de 15–30 min a tela **não é** "ao
  vivo", e o rótulo atual passaria a ser impreciso (§15);
- exige a tabela nova `marts.fact_tiktok_hourly`, que **não é criada** em S1–S3.

**Consequência a declarar:** ao fim dos três gates, três das quatro superfícies do
Gate G4 voltam a funcionar (`/operacoes`, `/brand-detail`, `/inteligencia`) e
**`/tempo-real` continua indisponível em produção**. Isso deve constar de qualquer
relatório de encerramento — nada de "camada de serving concluída" com uma tela ainda
sem fonte.

## 26. Execução do Gate S1 — registro do piloto (11/08/2026)

**Veredito: `PARTIAL — PILOTO VALIDADO`.** O **piloto técnico foi validado**: migration,
carga histórica, incremental, isolamento da janela e reconciliação passaram, todos com
prova registrada abaixo.

O resultado geral permanece `PARTIAL` por **um único motivo**: a execução dentro de um
**worker Airflow real não foi comprovada**. Nenhuma infraestrutura Airflow, DAG,
connection, secret ou pool foi validada — §24 e §24.1 seguem valendo integralmente, e o
`PARTIAL` é exatamente o desfecho que o §25 previa para esse cenário.

`/operacoes` continuar lendo o Data Mart **não é falha nem pendência do S1**. A troca do
endpoint pertence explicitamente ao Gate S2 (§25) e é a fronteira esperada entre os dois
gates.

### 26.1 Preflight — a barreira que importou

A primeira tentativa deste piloto foi **abortada** como `BLOCKED` porque a ingestão
Shopee estava ativa. A evidência não veio de "silêncio de WAL", que é critério ruim: o
Data Mart é uma **réplica física de leitura** (`pg_is_in_recovery() = true`), e o
processo `startup` que aplica o WAL mantém `AccessExclusiveLock` de forma rotineira —
24 deles na primeira medição. Confundir isso com DDL de cliente produziria bloqueios
falsos todos os dias.

Os dois critérios que realmente funcionam:

1. **Crescimento de relação por amostragem dupla.** Em 46 s, `raw.shopee_order_item_export`
   cresceu 3,05 MB e `gold.item_ciclo` 107 MB — ingestão inequívoca. Na rodada aprovada,
   o total em bytes das tabelas `%shopee%` ficou **idêntico** entre as duas leituras.
2. **Fingerprint duplo da fonte**, separado por ≥ 30 s, na janela fechada.

Duas armadilhas de medição foram encontradas e corrigidas no próprio preflight, e valem
como advertência para o S2:

- `mode LIKE '%Exclusive%'` **sem** `locktype = 'relation'` conta o `ExclusiveLock` que
  toda sessão mantém sobre o próprio `virtualxid`. Isso produziu "19 locks de escrita"
  onde havia zero. O filtro por `locktype` é obrigatório.
- um regex de verbo de escrita sobre `pg_stat_activity.query` casa com nomes de coluna:
  três "sessões de escrita" eram consultas `SELECT` do Metabase contendo `created_at`.

### 26.2 Fingerprints da fonte

Janela fechada `ref_date <= 2026-08-10`. As 4 linhas de 11/08 foram **ignoradas** em
todas as etapas — dia corrente incompleto, recusado por desenho.

| Momento | Linhas | Chaves | Datas | Marcas | `gmv` | `paid_orders` | Checksum |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Preflight, leitura 1 | 1.621 | 1.621 | 471 | 4 | 38.238.512,60 | 452.665 | `fe649ede…` |
| Preflight, leitura 2 (+35 s) | 1.621 | 1.621 | 471 | 4 | 38.238.512,60 | 452.665 | `fe649ede…` |
| Revalidação pós-backfill | 1.621 | 1.621 | 471 | 4 | **38.238.360,80** | **452.663** | `f93e9a33…` |

Zero duplicidade em `(ref_date, brand)`, zero nulo obrigatório, zero negativo, 471/471
dias com linha, 719 `roas` não nulos (razão — **nunca somada**).

### 26.3 Migration 006

`alembic upgrade 006`, tentativa única, exit 0, `005 → 006`. Relações em `marts`:
**31 → 32**, nenhuma desaparecida, nenhuma existente alterada.

Contrato físico conferido no banco: 9 colunas; `roas NUMERIC(12,4)` **nullable**, todas
as demais de negócio `NOT NULL`; `synced_at` com `DEFAULT NOW()`; PK
`pk_fact_ml_gestao_diaria (ref_date, brand)`; índice `idx_fmgd_brand_ref_date (brand, ref_date)`;
5 CHECKs, sendo 4 com `<> 'NaN'` explícito; comentário de tabela e 3 de coluna. Tabela
criada **vazia**.

### 26.4 Publicações

| Etapa | `run_id` | Janela | Apagadas | Publicadas | `EXCEPT` |
| --- | --- | --- | --- | --- | --- |
| Backfill histórico | `s1t2-bf1` | 27/04/2025 – 10/08/2026 | 0 | 1.621 | `(0, 0)` |
| Incremental | `s1t2-inc1` | 04/08/2026 – 10/08/2026 | 28 | 28 | `(0, 0)` |

Reconciliação independente do módulo (leitura separada em cada engine, checksum de
negócio normalizado para os tipos do destino) fechou em **todos** os campos nas duas
janelas: histórica `59ecb562…` e incremental `159473c1…`, idênticos entre origem e
destino.

**O `DELETE` ficou provadamente restrito à janela.** As 1.593 linhas anteriores a
04/08 mantiveram checksum `dad5e010…` inalterado e seguem marcadas com `s1t2-bf1`,
enquanto as 28 linhas da janela passaram a `s1t2-inc1`.

### 26.5 Idempotência sob fonte estável — validação residual

A segunda publicação histórica controlada **não ocorreu**, porque sua condição não se
cumpriu: entre o backfill e a revalidação, a fonte mudou.

A deriva foi localizada e é pequena e explicável — **2 chaves**, em 06/08 e 08/08, cada
uma perdendo 1 pedido pago e R$ 75,90 de GMV. É **maturação retroativa real** de status
no ML sobre dias já fechados, e é exatamente o fenômeno que justifica o lookback de 7
dias (§7). O incremental subsequente **corrigiu integralmente** a diferença: **−R$ 151,80**
em `gmv` e **−2 pedidos**, restaurando paridade total com a fonte.

O que **não** foi demonstrado em produção, então, é a **idempotência dos campos de
negócio sob uma fonte estável**: duas execuções consecutivas sobre a mesma fotografia da
origem deixando as sete colunas de negócio inalteradas.

**O critério correto não é igualdade byte a byte da linha completa.** `synced_at` e
`source_run_id` são campos de **auditoria** e mudam a cada execução por desenho — é assim
que se rastreia qual run publicou cada linha (foi justamente o que provou o isolamento da
janela em §26.4). Uma contraprova que exigisse a linha inteira idêntica estaria medindo a
coisa errada e reprovaria um pipeline correto.

O piloto **comprovou**: convergência (o destino alcança a fonte após mudança retroativa),
unicidade da chave, reconciliação origem × destino nas duas janelas e isolamento do
`DELETE` à janela publicada.

**Essa validação residual não bloqueia o início do Gate S2.** Quando houver janela
operacional estável, a contraprova deve comparar **apenas as chaves e os sete campos de
negócio**, admitindo explicitamente a atualização de `synced_at` e `source_run_id`.

### 26.6 Estado final do destino

1.621 linhas; 0 duplicidade de PK; 0 nulo obrigatório; 0 negativo; **0 linha de 11/08
ou posterior**; 902 `roas` NULL preservados; `synced_at` e `source_run_id` presentes em
100% das linhas.

### 26.7 O que este gate NÃO provou

O primeiro item é a **razão do `PARTIAL`**. Os demais são limites conhecidos ou fronteiras
de escopo, não pendências do S1.

- **Airflow — a razão do `PARTIAL`**: nenhuma DAG, connection, secret ou pool foi criado;
  nenhum `SELECT 1` rodou dentro de um worker real. §24 inalterado. Nada aqui afirma que
  o Airflow existe, está configurado ou tem conectividade.
- **`downgrade`**: escrito e restrito aos dois objetos do S1, mas **nunca executado**.
- **Idempotência sob fonte estável**: validação residual, definida em §26.5. Não bloqueia
  o S2.
- **Execução sem VPN**: o piloto rodou de máquina local com VPN. O Render **continua**
  sem alcançar o Data Mart — é o problema que o S2 resolve ao trocar a fonte do endpoint
  para o Neon.

Fora desta lista, por ser **fronteira de escopo e não lacuna**: `/operacoes` segue lendo
`gold.ml_gestao_diaria` via `gold_service.py` (11 referências, arquivo intocado) e nenhum
efeito no site era esperado. Trocar o endpoint é o objetivo declarado do Gate S2.
- **Ambiente**: o módulo exige `DATABASE_URL` e `DATAMART_DATABASE_URL` no ambiente,
  **sem fallback** e sem carregar `.env` por conta própria. `python -m pipelines.sync_ml_gestao_diaria`
  falha com exit 2 se as variáveis não estiverem exportadas — comportamento correto para
  Render/Airflow, mas que exige carregá-las explicitamente em execução local.

### 26.8 Estado final — Gate S1 encerrado

**Gate S1 encerrado. Resultado: `PARTIAL — PILOTO VALIDADO`.**

A camada de serving tem sua **primeira tabela disponível no Neon**:
`marts.fact_ml_gestao_diaria`, com **1.621 linhas até 10/08/2026**, **zero duplicidade**
na chave `(ref_date, brand)`, **zero linha do dia corrente** e **origem e destino
reconciliados** nas duas janelas (histórica e incremental), agregados e checksum de
negócio incluídos.

**O Gate S2 não foi iniciado**, e está **tecnicamente desbloqueado** para desenhar e
migrar `/operacoes`: existe tabela no Neon, contrato físico verificado, módulo de sync
com reconciliação e isolamento de janela provados.

**Desbloqueio técnico não é afirmação de infraestrutura.** Nada aqui diz que o Airflow
existe, está configurado ou tem conectividade — §24 permanece a referência, e a prova
operacional continua pendente de nome/URL, acesso, modelo de hospedagem e `SELECT 1`
executado de dentro do worker real.

## 27. Gate S2, Task 1/3 — contrato, schemas e sync locais (11/08/2026)

**Estado: implementada localmente, aguardando revisão.** Nenhuma migration aplicada,
nenhuma tabela TikTok criada no Neon, endpoint ainda em `gold.*`, Airflow não comprovado.
O Neon segue em `alembic_version = 006` e `marts` segue com 32 relações.

### 27.1 Auditoria read-only das duas fontes

Janela fechada `date <= 2026-08-10`. As duas são **TABELAS** (não views) e **nenhuma tem
PK ou UNIQUE físico**: o grão é convenção na Gold e passa a ser restrição no destino.

Números **já com a allowlist oficial de cinco marcas** aplicada (ver §27.8).

| | `gold.tiktok_brand_daily` | `gold.tiktok_creator_daily` |
| --- | --- | --- |
| grão | `(date, brand)` | `(date, brand, creator)` |
| linhas / chaves | **1.546 / 1.546** | **184.252 / 184.252** |
| duplicidade | **0** | **0** |
| intervalo | 05/10/2025 – 10/08/2026 | 07/10/2025 – 10/08/2026 |
| datas / dias sem linha | 310 / **0** | 308 / **0** |
| marcas | **5** (allowlist oficial) | **5** (allowlist oficial) |
| colunas totais | 68 | 18 |
| colunas consumidas | 37 | 9 |
| nulos em obrigatórias | **0** (21 colunas) | **0** (9 colunas) |
| NaN | **0** (35 colunas) | **0** (6 colunas) |
| linhas descartadas pelo filtro de marca | 398 | 13.282 |
| criadores distintos | — | 22.074 |

Quatro achados que moldaram o desenho:

1. **Cobertura de ~10 meses, não 13.** As fontes começam em 05/10 e 07/10/2025 — **87 e
   89 dias depois** do piso de 13 meses. O piso vale "quando a fonte possuir essa
   cobertura", então o backfill leva **todo o histórico disponível** e o diagnóstico
   **declara o déficit** em cada execução, em vez de omiti-lo.
2. **`total_live_minutes` tem 2 valores negativos**, um deles **−29.545.461**, em
   03/04/2026 e 06/05/2026, ambos em marcas do escopo. A soma histórica da coluna fica
   **negativa** (−8.485.885): os negativos somam −59.026.387 contra +50.540.502 dos
   válidos. É defeito de dado na ingestão TikTok. Impacto no payload atual de
   `/operacoes`: **zero**, porque o bloco `lives` usa janela de 30 dias e as linhas ruins
   são de abril/maio. Consequência de desenho: **proibido CHECK `>= 0`** nessa coluna —
   copiar exatamente é o contrato desta task, e corrigir a origem pertence ao pipeline de
   ingestão, não à camada de serving.
3. **`total_fees` é negativa em 1.529 de 1.546 linhas** (mín. −266.342,00). É taxa;
   negativo é o esperado. Também sem CHECK de não-negatividade.
4. **14 colunas de demografia são 100% nulas** e `visitors`/`customers` são nulas em
   68,6% e 48,2%. `brand-detail` calcula médias ponderadas sobre elas, que hoje retornam
   sempre `NULL`. São opcionais e ficam fora da exigência de não-nulo.

### 27.2 A regra de nulabilidade

Mecânica e baseada em evidência, a mesma do Gate S1: **NOT NULL somente onde a auditoria
provou zero nulos em todo o histórico.** Resultado: 21 de 37 colunas NOT NULL na
`brand_content`, e **todas as 9** na `creator_daily`, que não tem um único nulo.

### 27.3 Contrato congelado de `/operacoes`

`apps/api/tests/test_operacoes_contract.py`, **30 testes**, sem banco e sem produção
(que responde 500 no Render). Intercepta `gold_service._query` — o ponto certo, porque
`_query` roteia `gold.*` para o `datamart_engine` e uma Session falsa nem seria
consultada — e congela `date.today()` em 11/08/2026.

Congela os cinco blocos (`alertas`, `ml_velocity`, `creators`, `lives`, `tk_daily`) campo
a campo: nomes, tipos, nulabilidade, arredondamentos (1 casa em `pct_live`, 2 nos
demais), ordenação, `LIMIT 30`, `HAVING SUM(total_lives) > 0`, as janelas de 7/14/30
dias, os filtros de marca e o comportamento de lista vazia.

Duas sutilezas ficaram explicitamente congeladas, porque mudá-las na troca de fonte
alteraria o payload:

- **falsy-para-`None`**: `if r.get("roas_7d")` trata **0 como ausente**. Um ROAS de
  exatamente 0 sai como `null`, não como `0.0`. Vale igual para `gpm_video`, `pct_live`,
  `gmv_per_live` e `gmv_per_minute`;
- **precedência do `elif`**: gasto sem venda gera `ad_sem_gmv` (crítico) e **nunca** é
  rebaixado a `roas_baixo`, mesmo quando as duas condições valeriam.

Há também um teste que prova que `get_operacoes` **ainda lê `gold.*`** e não menciona
`marts.*` — a troca é a Task 3/3.

### 27.4 Migrations 007 e 008 — escritas, não aplicadas

Cadeia linear `006 → 007 → 008`, head único `008`, verificada por `alembic history` sem
aplicar.

| | 007 | 008 |
| --- | --- | --- |
| tabela | `marts.fact_tiktok_brand_content_daily` | `marts.fact_tiktok_creator_daily` |
| PK | `(date, brand)` | `(date, brand, creator)` |
| índice | `idx_ftbcd_brand_date (brand, date)` | `idx_ftcd_brand_date (brand, date)` |
| colunas | 37 negócio + 2 auditoria | 9 negócio + 2 auditoria |

Ambas: criação **fail-fast** sem `IF NOT EXISTS`, zero `SELECT` e zero DML, nada
executado no import (verificado por AST), `downgrade` restrito aos próprios dois objetos.

**`NUMERIC` sem escala declarada**, igual à fonte. Fixar `NUMERIC(18,2)` arredondaria e
quebraria a igualdade de payload que o Gate S2 precisa provar — é uma diferença
deliberada em relação à 006.

**Por que o nome tem `content`.** As colunas de valor destas tabelas pertencem à linhagem
de **conteúdo** do TikTok, que não é o GMV oficial do marketplace: o canônico é calculado
da Raw com `sub_total` e allowlist de status, a Gold calcula sobre o valor antigo
(≈`total_amount`) e fica **+2,43%** acima, e `gmv_video`/`gmv_live`/`gmv_card` **não
decompõem** o GMV de pedidos. O sufixo existe para que ninguém some `gmv` desta tabela
como GMV do canal.

### 27.5 Sync — um módulo, duas specs literais

`pipelines/sync_tiktok_serving.py`. As duas tabelas têm mecânica idêntica e diferem só em
origem, destino, chave e colunas: duas cópias de 400 linhas envelheceriam divergindo, e
um framework genérico seria abstração prematura. O meio-termo é **um módulo com duas
`TableSpec` literais e fixas** — nada descoberto em runtime, e uma terceira tabela exige
escrever a spec à mão.

Contratos implementados: Data Mart aberto `readonly=True`; diagnóstico como **padrão** e
escrita só sob `--apply`; janela sempre terminando em **D−1** com o dia corrente recusado
explicitamente; incremental móvel com **mínimo de 7 dias fechados** (lookback menor é
erro); **transação única e advisory lock próprio por tabela** (`907120007` e `908120008`,
distintos entre si e do `906120006` do S1); staging `pg_temp ... ON COMMIT DROP`;
`DELETE` só da janela + `INSERT` com lista explícita de colunas; validação de chave,
nulos, NaN, negativos e cobertura antes de qualquer escrita; agregados fonte × staging ×
destino; `EXCEPT` bidirecional; rollback integral; erros sanitizados; zero
retry/backoff/sleep/agendamento; zero escrita em Gold/Raw/Silver; zero dependência de
Airflow.

**Por que `DELETE` da janela e não `ON CONFLICT DO UPDATE`:** upsert **não apaga**. Se uma
linha desaparecer da fonte dentro da janela — dia reprocessado, criador removido, marca
sem movimento — o upsert a deixaria órfã no destino para sempre. Dois testes provam a
remoção retroativa refletida, um por tabela.

### 27.6 Por que a mudança de GMV não pode entrar no S2

A decisão de incluir frete altera `pipelines/connectors/tiktok/connector.py` e **muda o
GMV retroativamente em toda a série**, exigindo recarga do histórico. Se ela entrasse
nesta frente, as duas provas centrais do S2 ficariam impossíveis de interpretar:

1. **a comparação Gold × Marts** compararia números calculados por regras diferentes; uma
   divergência não distinguiria erro de cópia de mudança de definição;
2. **a garantia de payload idêntico** perderia sentido: o payload mudaria de propósito, e
   o teste de contrato — cuja única função é falhar quando a troca de fonte altera algo —
   teria de ser reescrito exatamente no momento em que precisa ser imutável.

São mudanças de natureza distinta: uma troca **de onde o dado vem**, a outra troca **o que
o dado significa**. Misturá-las tira a capacidade de saber qual das duas quebrou. Produção
segue em `sub_total`, esta task copia exatamente o que a Gold serve, e a escolha entre
`total_amount` e `sub_total + shipping_fee` continua pendente na frente separada.

### 27.7 Plano operacional da Task 2/3

Pré-condição, com a lição do S1: confirmar ausência de escrita concorrente por
**crescimento de relação em amostragem dupla**, nunca por silêncio de WAL, e com filtro
`locktype = 'relation'` nos locks — o processo de replay da réplica mantém
`AccessExclusiveLock` rotineiramente, e `ExclusiveLock` sobre `virtualxid` existe em toda
sessão.

Sequência: fingerprint duplo das duas fontes (≥30 s) → `alembic upgrade 007` → conferir
contrato físico → `alembic upgrade 008` → conferir → diagnóstico de backfill das duas →
`--apply --backfill --table brand` → reconciliar → `--apply --backfill --table creator`
→ reconciliar → incremental de 7 dias nas duas → reconciliação final.

Volume esperado: **1.546** linhas na `brand_content` e **184.252** na `creator_daily`,
medidos em dry-run read-only com a allowlist. A `creator_daily` é ~119× a `brand_content`
e ~114× a fato do S1 — é a primeira publicação deste porte da camada de serving, e o
`statement_timeout` do destino já está em 300 s por isso.

**Corte comum explícito — obrigatório na Task 2/3.** As duas tabelas Gold não são
carregadas em sincronia: em 12/08/2026, `tiktok_brand_daily` já tinha 11/08 e
`tiktok_creator_daily` não. A validação de cobertura recusa a janela nesse caso, e isso é
o comportamento correto — **não deve ser afrouxado no código**. A regra fica no preflight,
que calcula:

```
common_date_to = min(D−1,
                     MAX(date) de gold.tiktok_brand_daily,
                     MAX(date) de gold.tiktok_creator_daily)
```

A primeira carga das duas tabelas usa **esse mesmo corte comum, passado explicitamente**
via `--date-from/--date-to`, nunca a janela default. Assim as duas fatos ficam alinhadas na
mesma data de fechamento, e a reconciliação entre elas é interpretável.

**Se alguma das fontes não tiver cobertura contínua até `common_date_to`, a operação
para.** Não se publica janela com dia vazio, e não se ajusta a validação para aceitá-la: um
buraco no meio da série é achado a investigar na origem, não obstáculo a contornar no
serving.

Contraprova de idempotência pendente do S1: comparar **apenas chaves e campos de
negócio**, admitindo atualização de `synced_at` e `source_run_id`.

### 27.8 Minimização de dados — somente as cinco marcas oficiais

A Gold contém marcas além das cinco autorizadas, e **nenhuma delas tem consumidor na
Torre**: `/brand-detail` recusa marca fora da lista e `/operacoes` filtra por
`BRANDS_IN_SCOPE`. Copiar o excedente não serviria a nenhuma tela e ampliaria sem
necessidade a superfície de dado pessoal, porque `creator` é handle público
potencialmente identificável.

O sync usa a **allowlist oficial**, importada de
`pipelines.connectors.tiktok.connector.BRANDS_IN_SCOPE`. Não há uma segunda lista no
módulo de serving: um teste verifica a **identidade** (`is`) com a tupla do conector, e
outro proíbe qualquer marca literal no código. Uma terceira cópia divergiria da primeira
no dia em que uma marca entrasse ou saísse, e o sync passaria a publicar um conjunto que
nenhum consumidor autoriza.

O filtro é **parametrizado** — `brand = ANY(%(brands)s)`, com a lista indo como parâmetro
nas duas queries. Interpolar `IN ('a','b')` funcionaria hoje e viraria injeção no dia em
que a lista viesse de fora do código; há teste garantindo que nenhum valor da allowlist
aparece no texto do SQL. Como defesa em profundidade, `validate_source_rows` reprova a
fotografia se qualquer marca externa aparecer, e a mensagem informa **a quantidade, nunca
os nomes**.

Efeito medido, em janela histórica até 10/08/2026: **398** linhas descartadas na
`brand_daily` (1.944 → 1.546, −20,5%) e **13.282** na `creator_daily`
(197.448 → 184.252, −6,7%); criadores distintos caem de 22.913 para **22.074**. O
`source_min_date` das duas fontes **não muda** com o filtro (05/10 e 07/10/2025), e os
dois valores negativos de `total_live_minutes` continuam dentro do escopo — seguem sendo
copiados exatamente, como dívida da origem.

### 27.9 Reconciliação exata em `Decimal`

A reconciliação **não usa `float` em nenhum caminho**. `float` tem 53 bits de mantissa, e
somar ~184 mil valores monetários em ponto flutuante acumula erro de representação; o
Postgres soma `NUMERIC` em decimal exato. A comparação confrontaria dois números
calculados em aritméticas diferentes e poderia divergir por centavos sem nada estar errado
no dado — ou, pior, esconder uma divergência real dentro da margem.

`_dec()` preserva o `Decimal` que vem do psycopg2, converte inteiro exatamente, usa
`Decimal(str(x))` para os demais tipos (nunca `Decimal(float)`, que herdaria o erro
binário), recusa booleano e trata nulo como `Decimal("0")` **apenas nas somas**, onde o
contrato vigente do endpoint já faz `COALESCE(..., 0)`. Isso não afeta a contagem de
não-nulos das razões nem a validação de obrigatórias.

Os agregados **não são arredondados** antes da comparação — arredondar esconderia
divergência real dentro da tolerância. `aggregates_from_rows` e `aggregates_from_table`
produzem `Decimal` dos dois lados, e `compare_aggregates` compara valor exato. A
comparação é de **valor**, não de escala: `Decimal("100")` e `Decimal("100.0000")` são
iguais, o que é o comportamento correto para `SUM` vindo de colunas com escalas
diferentes. `_print_agg` formata com `:f` para evitar notação científica, lendo do
dicionário sem escrever de volta.

**Contraprova adversarial**, com o volume real da fato de criador — 197.448 ocorrências de
`12345.67891`:

| Aritmética | Total |
| --- | --- |
| `Decimal` (exato) | `2437629609.42168` |
| `float` + `round(…, 4)` (implementação anterior) | `2437629609.4189` |
| diferença | **−0,00278** |

Não é erro de arredondamento benigno: é desvio real, do tipo que uma tolerância monetária
silenciosa esconderia. No caso pequeno, `sum(0.1 × 10)` em `float` dá
`0.9999999999999999` e nunca é igual a `1.0`; em `Decimal`, dá exatamente `1.0`.

Preservados sem mudança: razões nunca somadas, contagem de razões não nulas, NaN recusado
(detectado por `Decimal.is_nan()`, sem passar por `float`), colunas assinadas isentas de
CHECK de não-negatividade e `EXCEPT` bidirecional restrito às colunas de negócio.

## 28. Preflight da Task 2/3 bloqueado por VPN, e o hardening que ele revelou (12/08/2026)

**Estado: `BLOCKED`. Nenhuma escrita ocorreu** — zero migration, zero `--apply`, zero DDL
ou DML. O Neon segue em `alembic_version = 006`, com as duas tabelas do S2 ausentes, e
nenhuma autorizacao de migration ou backfill foi consumida.

### 28.1 O que bloqueou

Exclusivamente a **VPN desconectada**. A causa foi confirmada, nao suposta: nenhum
adaptador de tunel ativo, **nenhuma rota para a faixa privada** do Data Mart e nenhum
cliente de VPN em execucao. O lado do Neon passou integralmente — Alembic linear
`006 -> 007 -> 008` com head unico, os seis objetos de 007/008 ausentes um a um, zero
constraint ou indice de migration parcial, grants suficientes (`marts` CREATE/USAGE,
`alembic_version` SELECT/UPDATE e **TEMP no database**, que a staging `pg_temp` exige),
zero backend concorrente, zero lock de escrita e zero advisory lock em uso.

Sem ler a fonte nao existe `common_date_to`, nem fingerprint duplo, nem verificacao de
cobertura. Nenhum `GO` foi emitido com numeros da rodada anterior.

Um dado de dimensionamento ficou resolvido no caminho, e e' boa noticia: `marts` ja contem
`fact_tiktok_product_daily` com **208.451 linhas em 73 MB**. O porte da `creator_daily`
(~184 mil linhas) tem precedente no mesmo banco, o que reduz bastante o peso do risco de
`statement_timeout=300s`.

### 28.2 O finding: a mensagem de erro vazava topologia

O timeout expos **hostname e IP privado** na saida, e a checagem confirmou que o
sanitizador entao em uso nao os removia: ele redigia `usuario:senha@` e **preservava todo
o resto**. Num log de execucao real — Render, Airflow, agendador — isso publica topologia
interna para quem tiver acesso ao log, sem nenhum ganho diagnostico.

**Corrigido nos dois modulos de serving**, com a regra invertida: mensagem de conexao
**nunca e' ecoada**; e' classificada numa das cinco categorias fixas.

| Categoria | Quando |
| --- | --- |
| `falha de autenticacao no banco: credencial recusada pelo servidor.` | `password authentication failed`, `authentication failed`, `no password supplied`, `role "..." does not exist` |
| `conexao recusada por regra de acesso do servidor (pg_hba.conf).` | `pg_hba.conf`, `no pg_hba entry` |
| `servidor inalcancavel ou timeout de conexao (verifique a VPN).` | `timed out`, `timeout expired`, falha de resolucao de nome, `no route to host`, rede inalcancavel |
| `conexao recusada pelo servidor (porta fechada ou servico parado).` | `connection refused` |
| `falha de conexao com o banco.` | demais falhas de conexao e qualquer mensagem com topologia |

Formatos sensiveis cobertos: DSN completa (`postgresql://` e `postgres://`), `server at`,
IPv4, IPv6 (forma completa e comprimida, com ou sem colchetes), porta (`port=` e
`port N`) e as chaves libpq `host=`, `hostaddr=`, `user=`, `password=`, `dbname=`,
`passfile=`, `sslcert=`, `sslkey=`. Nome de database tambem sai: `database "..." does not
exist` e' classificado.

**Mensagens seguras continuam legiveis**, porque sao o que serve para diagnosticar:
divergencia de agregado, violacao de constraint, cobertura incompleta, valor negativo e
`canceling statement due to statement timeout` passam intactas. Duas armadilhas de
deteccao foram evitadas de proposito: um horario como `10:20:30` nao e' confundido com
IPv6 (a deteccao exige `::` ou pelo menos cinco grupos) e uma versao como `17.9` nao e'
confundida com IPv4 (exige quatro grupos pontuados). Sem esses cuidados, o hardening
apagaria mensagens legitimas.

Tambem por contrato: `str(exc)` e nunca `repr(exc)` — que carrega os argumentos da excecao
—, nunca `exc.__cause__` ou traceback, que guardam a mensagem nativa completa, e nenhum
log auxiliar. O `print` de falha do CLI usa exclusivamente o sanitizador, e um teste ponta
a ponta injeta a falha de conexao e verifica o `stderr`.

O texto das cinco categorias e' **identico nos dois modulos** — o bloco foi copiado
literalmente, e um teste cruzado importa o outro modulo e compara categoria por categoria,
para que a mesma falha produza a mesma mensagem nas duas frentes.

### 28.3 Isto nao desbloqueia a Task 2/3

O hardening corrige uma exposicao de log; **nao substitui o preflight**. O mesmo preflight
read-only precisa ser **repetido integralmente apos a VPN voltar**: corte comum,
fingerprint duplo com intervalo de 30 s, cobertura, duplicidade, allowlist e concorrencia
no Data Mart. So um `GO` daquele preflight autoriza aplicar 007/008.
