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

Os três são sequenciais, e o S1 é pré-requisito duro. **Estado em 13/08/2026:** S1 encerrado como `PARTIAL — PILOTO VALIDADO` (§26), S2 com as Tasks 1/3 e 2/3 concluídas (§27 e §29) e a Task 3/3 com backfill completo e `/operacoes` validado localmente, aguardando versionamento (§30 o `BLOCKED` que a motivou, §31 a correção de convergência, §32 a carga e a validação); S3 não iniciado.

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

## 29. Gate S2, Task 2/3 — serving TikTok publicado e reconciliado (13/08/2026)

**`SUCCESS — SERVING TIKTOK PUBLICADO E RECONCILIADO`.** Migrations 007 e 008 aplicadas
numa unica tentativa, as duas fatos carregadas e reconciliadas, e **nenhum endpoint
trocado** — `/operacoes` e `/brand-detail` continuam lendo `gold.*`, o que e' a Task 3/3.

### 29.1 Corte comum e janelas

`common_date_to = 2026-08-11`, calculado na execucao: D-1 era 12/08, a `brand_daily` ja
tinha 12/08 e a `creator_daily` nao. O corte recuou para o dia que **ambas** cobrem
integralmente, em vez de forcar as duas ate uma data que uma delas nao possui.

| | Janela | Linhas | Datas |
| --- | --- | --- | --- |
| `brand_content` | 2025-10-05 .. 2026-08-11 | **1.551** | 311 |
| `creator_daily` | 2025-10-07 .. 2026-08-11 | **185.035** | 309 |

12/08 ficou de fora desta carga de proposito. O incremental de sete dias o absorve na
proxima execucao, sem intervencao.

### 29.2 Preflight final antes da primeira escrita

24 verificacoes, todas aprovadas, com **hashes completos** conferidos contra o baseline —
nao apenas prefixos. Fingerprints reproduzidos identicos nas janelas congeladas:
`brand` chaves `234a15069a9c84b0d37d5b2a4e3611ef` e negocio `704c57deeb9f122c2da98dfae6b7d33c`;
`creator` chaves `6ad7dacde5b630a619d34c1434c6b7fe` e negocio `ae353fee616a4123565d9350b46f44f5`.

Concorrencia verificada por amostragem dupla de 36 s: tamanho e contagem bruta das duas
fontes identicos, zero lock de escrita de cliente com filtro `locktype='relation'`, zero
advisory lock do serving, zero outro backend no Neon.

### 29.3 Migration

`alembic upgrade 008`, uma tentativa, exit 0, `006 -> 007 -> 008`. Relacoes em `marts`:
**32 -> 34**, nenhuma desaparecida.

Contrato fisico conferido no banco: `brand_content` com **39 colunas** e **13 CHECKs**;
`creator_daily` com **11 colunas** e **5 CHECKs**; PKs `(date, brand)` e
`(date, brand, creator)`; indices `idx_ftbcd_brand_date` e `idx_ftcd_brand_date`;
`NUMERIC` sem escala declarada nas duas; nulabilidade exatamente igual as colunas
opcionais da spec; `total_fees` e `total_live_minutes` **sem** CHECK de nao-negatividade,
como a auditoria exigia; ambas criadas **vazias**.

### 29.4 Publicacao e reconciliacao

| | `run_id` | Apagadas | Publicadas | `EXCEPT` | Duracao |
| --- | --- | --- | --- | --- | --- |
| Brand | `s2t2-bf-brand` | 0 | **1.551** | `(0, 0)` | 7 s |
| Creator | `s2t2-bf-creator` | 0 | **185.035** | `(0, 0)` | 50 s |

Reconciliacao independente do modulo, em conexoes separadas por engine: contagem, chaves,
datas, marcas, min/max, duplicidade, nulos obrigatorios, NaN, cobertura e **todas** as
somas em `Decimal` (21 colunas na `brand_content`, 6 na `creator_daily`) conferem entre
origem e destino. Zero linha fora da janela, zero marca fora da allowlist, auditoria
completa em 100% das linhas, um unico `source_run_id` por tabela.

O risco de `statement_timeout=300s` nao se materializou: 50 s para 185 mil linhas, com a
leitura da fonte fora da transacao.

### 29.5 Uma divergencia de fingerprint que era de COLLATION, nao de dado

A reconciliacao da `creator_daily` acusou divergencia nos dois fingerprints, com **tudo o
mais identico** — contagens, somas `Decimal` e o `EXCEPT` bidirecional `(0,0)`.

A causa foi medida: **as duas bases tem collation diferente**. O Data Mart usa
`en_US.UTF-8` e o Neon usa `C.UTF-8`. O fingerprint e' `MD5(STRING_AGG(... ORDER BY l))`,
e o `ORDER BY` sobre texto depende de collation — logo o mesmo conjunto de linhas produz
digests diferentes. Forcando `COLLATE "C"` nos dois lados, os hashes batem exatamente:
chaves `37c25cd37c18fd38bae511d882bc7b7d` e negocio `02b68b9e1a5aba319c5045089ded355d` em
ambas as pontas.

A `brand_content` nao tropecou porque suas chaves (`date;brand`) sao digitos, hifens,
ponto-e-virgula e minusculas ASCII, que ordenam igual nas duas collations. Os handles de
criador tem maiusculas, pontuacao e digitos misturados, onde as regras divergem.

Prova definitiva, sem qualquer ordenacao SQL: as tuplas de chave e negocio das duas pontas
foram comparadas como conjuntos em memoria — **185.035 de cada lado, conjuntos identicos,
zero exclusiva em qualquer direcao, zero duplicata**.

**Licao para a Task 3/3 e para qualquer comparacao entre engines:** fingerprint por
`STRING_AGG` so e' comparavel entre bases com collation explicita (`COLLATE "C"`) ou por
comparacao de conjuntos. Sem isso, produz falso alarme em qualquer texto com maiuscula ou
pontuacao — e um falso alarme aqui teria parado a operacao sem motivo.

### 29.6 Isolamento e escopo

Inventario de `marts` comparado antes e depois: das 34 relacoes, **exatamente 2 mudaram de
contagem** — as duas recem-criadas. `marts.fact_ml_gestao_diaria` ficou **identica**: 1.621
linhas, intervalo 2025-04-27..2026-08-10 e checksum de negocio
`fe3ca591681649768b87dae936f00ef4` antes e depois. As 15 tabelas Shopee em `marts`
inalteradas. Database: 164,2 MB -> 197,2 MB; `brand_content` ocupa 544 kB e
`creator_daily` 32 MB.

Zero alteracao de codigo, SQL versionado, contrato, endpoint ou regra de negocio.
`gold_service.py` intocado, com 27 referencias a `gold.*`. O connector TikTok intocado: a
**decisao de frete no GMV permanece frente separada e inalterada**, e producao segue em
`sub_total`.

### 29.7 O que continua pendente

- **Task 3/3**: trocar a fonte de `/operacoes` para `marts.*`, com o contrato congelado de
  30 testes como criterio de payload identico. Nao iniciada.
- **Airflow**: nada provado. Nenhuma DAG, connection, secret ou pool. §24 inalterado.
- **Idempotencia sob fonte estavel**: continua sem contraprova. Nenhuma segunda execucao
  foi feita, por decisao explicita desta operacao.
- **12/08 na `brand_daily`**: fora desta carga, entra no proximo incremental.

## 30. Gate S2, Task 3/3 — BLOCKED por deriva de valor dentro da janela coberta (13/08/2026)

**`BLOCKED`. Nenhuma linha de codigo foi alterada.** `get_operacoes` continua lendo
`gold.*`. Zero escrita, zero sync, zero migration, zero commit.

A troca de fonte parou no criterio que existe justamente para isso: **houve diferenca
material dentro de uma janela coberta pelas duas fontes**. O importante e' que a
investigacao mudou o entendimento do problema — nao e' atraso de um dia, e' **reafirmacao
retroativa profunda**.

### 30.1 Frescor: Gold ja avancou

| Tabela | Gold | Marts | Defasagem | Linhas a mais na Gold |
| --- | --- | --- | --- | --- |
| `ml_gestao_diaria` | 2025-04-27..**2026-08-13** | ..2026-08-10 | **3 dias** | 12 |
| `tiktok_brand_daily` | 2025-10-05..**2026-08-12** | ..2026-08-11 | 1 dia | 5 |
| `tiktok_creator_daily` | 2025-10-07..**2026-08-12** | ..2026-08-11 | 1 dia | 662 |

Corte comum entre as tres copias: **2026-08-10**, limitado pela fato do S1, que nao recebeu
incremental desde o backfill de 11/08.

### 30.2 O metodo: SQL de producao nas duas fontes

Em vez de reescrever as consultas a mao, o teste **capturou as cinco consultas que
`get_operacoes` monta**, executou cada uma nas duas fontes (a versao Marts com apenas a
troca de nome de relacao) e **reconstruiu o payload com o mesmo codigo de
pos-processamento**. Comparar dois payloads produzidos pelo codigo de producao elimina a
chance de o proprio teste introduzir a diferenca.

### 30.3 O achado: nao e' mapeamento, e' reafirmacao retroativa

Comparacao chave a chave, por conjunto (sem ordenacao nem collation), dentro do corte comum:

| Tabela | Chaves Gold / Marts | So na Gold | So em Marts | **Valor diferente** |
| --- | --- | --- | --- | --- |
| `ml_gestao_diaria` | 1.621 / 1.621 | 0 | 0 | **64** |
| `tiktok_brand_daily` | 1.546 / 1.546 | 0 | 0 | **23** |
| `tiktok_creator_daily` | 184.257 / 184.257 | 0 | 0 | **6** |

**Todas as chaves sao comuns nas tres tabelas.** Erro de mapeamento ou de coluna produziria
divergencia sistematica — colunas inteiras, ou chaves faltando. O que existe e' um punhado
de chaves com valor alterado: a Gold **reafirmou** numeros de dias ja fechados depois de a
copia ter sido feita.

Magnitude, dentro do corte comum:

| Tabela | Metrica | Gold | Marts | Diferenca |
| --- | --- | --- | --- | --- |
| `ml_gestao_diaria` | `gmv` | 38.232.729,65 | 38.238.360,80 | **−5.631,15 (−0,0147%)** |
| | `paid_orders` | 452.591 | 452.663 | −72 (−0,0159%) |
| `tiktok_brand_daily` | `gmv` / `orders` | — | — | **0,0000% (identicos)** |
| | `new_videos_posted` | 712.771 | 712.741 | +30 (0,0042%) |
| `tiktok_creator_daily` | `gmv_total` | 81.006.826,52 | 81.006.709,62 | +116,90 (0,0001%) |
| | `views_video` | 1.488.273.035 | 1.488.259.447 | +13.588 (0,0009%) |

Percentualmente minusculo, **mas visivel no payload**: com o corte comum,
`ml_velocity[3].orders_7d` sai 1.653 na Gold e 1.656 em Marts, e `creators[3].videos` sai
166 contra 165. Numa torre de controle, numero exibido que muda conforme a fonte e'
material por definicao, independente do percentual — e o contrato congelado nao admite
alteracao de payload.

### 30.4 A consequencia estrutural: o lookback de 7 dias nao converge

Este e' o achado que ultrapassa a Task 3/3. Medindo a data mais antiga cujo valor mudou:

| Tabela | Data mais antiga alterada | Horizonte | Lookback de 7 dias resolve? |
| --- | --- | --- | --- |
| `ml_gestao_diaria` | 2026-06-03 | **68 dias** | **NAO** |
| `tiktok_brand_daily` | 2026-07-14 | **27 dias** | **NAO** |
| `tiktok_creator_daily` | 2026-08-10 | 0 dias | SIM |

`DEFAULT_LOOKBACK_DAYS = 7` nos dois modulos de sync foi dimensionado por hipotese
("late-arriving data"), nao por medicao. A medicao agora mostra que a `ml_gestao_diaria`
reafirma valores **ate 68 dias** para tras e a `tiktok_brand_daily` **ate 27**. Um
incremental de 7 dias corrigiria a ponta e **deixaria deriva permanente** nas datas de 8 a
68 dias — exatamente o tipo de buraco silencioso que `MAX(date)` nao detecta.

Nada disso foi alterado nesta task: corrigir dimensionamento de janela nao e' "ajustar um
numero", e' decisao de arquitetura da camada de serving.

### 30.5 O que fecharia a Task 3/3

Duas coisas, em ordem:

1. **Decidir a estrategia de convergencia** — lookback dimensionado pelo horizonte medido
   (>= ~70 dias para ML, >= ~30 para a brand), ou refresh completo periodico, ou
   reconciliacao que detecte deriva fora da janela. Sem isso, a camada de serving acumula
   divergencia invisivel a cada dia.
2. **Recarregar as tres tabelas** com backfill completo (nao incremental) e trocar o
   endpoint **imediatamente depois**, com a paridade medida na mesma execucao.

Enquanto isso, `/operacoes` no `gold.*` continua indisponivel em producao pelo Gate G4 — a
troca resolve a disponibilidade, mas nao ao custo de servir numero que difere da fonte.

### 30.6 Fronteiras preservadas

`gold_service.py` intocado (27 referencias a `gold.*`). Router, schema e frontend intocados.
`/brand-detail`, `/inteligencia` e `/tempo-real` intocados. Zero escrita nos dois bancos,
zero sync, zero migration, zero Airflow. **A decisao de frete no GMV TikTok permanece frente
separada e inalterada.** Nenhum handle de criador, linha individual, DSN ou topologia foi
impresso em nenhuma etapa.

## 31. Gate S2, Task 3/3 — correcao arquitetural de convergencia (13/08/2026)

**`READY FOR REVIEW — AGUARDANDO BACKFILL COMPLETO`.** As tres mudancas estao
implementadas e validadas **localmente**: politica temporal D-1 no fuso do Brasil,
lookback incremental de 90 dias e troca de `/operacoes` para `marts.*`. **Nada foi
publicado**: zero commit, zero push, zero deploy, zero escrita em banco, zero sync.

### 31.1 A causa do BLOCKED, e por que a resposta nao e' "um numero maior"

O §30 mediu reafirmacao retroativa da Gold **alem de sete dias**: ate 68 dias em
`ml_gestao_diaria` e 27 em `tiktok_brand_daily`. O lookback de 7 corrigiria a ponta e
deixaria deriva permanente no meio da serie — invisivel a checagem por `MAX(date)`,
porque a data maxima estaria certa e os valores nao.

A correcao tem duas partes independentes, e as duas eram necessarias:

- **teto D-1 no endpoint** resolve o dia corrente parcial, que nunca deveria ser servido;
- **lookback de 90 dias** resolve a convergencia da copia.

Nenhuma delas sozinha bastava: o teto sem o lookback serviria dado fechado porem
desatualizado; o lookback sem o teto continuaria mostrando um dia que muda sozinho ao
longo da tarde.

### 31.2 Politica temporal: D-1 em America/Sao_Paulo

`/operacoes` passa a ser uma **visao de dias fechados**. As cinco consultas ganharam teto
**inclusivo** em D-1; os limites inferiores **nominais** continuam os mesmos — 7 dias
(alertas, velocity, creators), 14 (tk_daily) e 30 (lives).

**O tamanho efetivo da janela mudou, e isso e' intencional.** Antes, por nao haver teto,
a presenca do dia corrente na fonte podia produzir **8, 15 ou 31 datas**. Agora as janelas
tem exatamente **7, 14 e 30 dias fechados**. Retirar o dia corrente e' alteracao
comportamental deliberada — o painel deixa de exibir um numero que mudava sozinho ao longo
do dia —, nao uma troca neutra.

O dia vem do **fuso do negocio**, nao do processo. `zoneinfo` e' biblioteca padrao, sem
dependencia nova. A razao e' concreta: a API roda em UTC no Render, os testes rodam no
fuso da maquina e um worker futuro pode rodar em qualquer lugar. Sem fuso explicito,
entre **21h e 00h no horario de Brasilia** (00h-03h UTC) o servidor ja teria virado o dia
e serviria uma janela deslocada — o painel mudaria sozinho no fim da tarde, sem que nada
tivesse acontecido no negocio. Ha teste para exatamente esse intervalo.

A mesma convencao vale nos dois modulos de sync: `date.today()` foi eliminado de ambos
(zero ocorrencias restantes), substituido por `hoje_operacional()`.

### 31.3 Lookback: default 90, piso 7

`DEFAULT_LOOKBACK_DAYS` passou de 7 para **90** nos dois modulos, com um comentario que
registra a medicao que motivou a mudanca — o numero deixou de ser hipotese e passou a ser
consequencia de dado observado.

Introduzi tambem `MIN_LOOKBACK_DAYS = 7` **explicito**, e isso corrige um acoplamento
perigoso: no modulo do TikTok a validacao era `if lookback_days < DEFAULT_LOOKBACK_DAYS`,
de modo que mudar o default para 90 teria **silenciosamente elevado o piso para 90** e
tornado impossivel qualquer reprocessamento menor. Piso e default agora sao constantes
separadas, com significados distintos: o piso e' contratual (menos que isso nao absorve
late-arriving data nenhum), o default e' a rotina.

Efeito colateral assumido e documentado: o piso do modulo do S1 subiu de 1 para 7. Uma
janela de 1 dia nao absorve reafirmacao alguma, e manter dois pisos diferentes em modulos
gemeos e' o tipo de assimetria que causa incidente. O teste que exigia a janela de 1 dia
foi reescrito para exigir a recusa, com a justificativa no proprio teste.

**90 dias e' a rotina, nao garantia eterna.** Reafirmacao mais antiga que 90 dias exige
**backfill historico periodico** — direcao recomendada: semanal, quando o Airflow existir.
Nada disso foi implementado: sem DAG, sem agendamento, sem Airflow.

### 31.4 Troca de fonte, restrita a `/operacoes`

As cinco consultas passaram a ler `marts.fact_ml_gestao_diaria`,
`marts.fact_tiktok_brand_content_daily` e `marts.fact_tiktok_creator_daily`. Zero `gold.`
e zero `raw.` no corpo de `get_operacoes`; `_uses_datamart` devolve `False` para as cinco,
o que faz `_query` usar a Session do Neon e **nunca abrir `datamart_engine`**. Um teste
executa `get_operacoes` com `datamart_engine = None` e uma Session falsa, e verifica que
as cinco consultas passam pela Session e o payload sai bem formado.

`/brand-detail`, `/inteligencia` e `/tempo-real` **continuam na gold** — a troca foi
cirurgica. Calculos, thresholds, filtros de marca, `GROUP BY`, `HAVING`, `LIMIT 30`,
ordenacoes, arredondamentos, comportamento falsy e textos de alerta: **intactos**.

### 31.5 O contrato congelado, e o que ele provou

O arquivo de contrato mudou em **duas coisas apenas**: o classificador reconhece `marts.*`
e as janelas exigem o teto D-1. **Nenhuma expectativa de payload foi alterada** — os
mesmos valores, campos, tipos, arredondamentos, limites e ordenacoes de antes, incluindo o
snapshot completo dos cinco blocos.

Foi assim que o contrato provou o que existia para provar: ao trocar a fonte, os testes de
payload continuaram verdes **sem edicao**, e os unicos que falharam foram os de fronteira,
que descreviam de onde o dado vinha. Se alguma expectativa de valor tivesse precisado
mudar, a troca teria alterado o contrato — e o certo seria parar.

### 31.6 O que isto NAO significa

**Nao ha paridade real de producao.** As tabelas `marts.*` seguem com os dados defasados
medidos no §30: a copia esta correta em estrutura e errada em frescor. Publicar agora
serviria numero desatualizado com aparencia de correto.

A proxima etapa exige **autorizacao de escrita** para backfill completo das tres tabelas,
com paridade medida na mesma execucao. So depois disso o endpoint pode ser publicado.

**Airflow continua inexistente e nao comprovado.** **A decisao de frete no GMV TikTok
permanece frente separada e inalterada.**

## 32. Gate S2, Task 3/3 — backfill completo e /operacoes validado (13/08/2026)

**`SUCCESS — BACKFILL COMPLETO E /OPERACOES PRONTO PARA VERSIONAMENTO`.** As tres tabelas
de serving foram recarregadas integralmente e reconciliadas, e `/operacoes` produz payload
**identico** ao da Gold. **Nada publicado**: zero commit, push ou deploy.

### 32.1 Dia operacional e cobertura

D-1 = **2026-08-12**, resolvido em America/Sao_Paulo. As tres fontes cobriam D-1 **sem
buraco algum** desde o primeiro dado — o corte comum recuado do §30 nao foi necessario.

Amostragem dupla com 35 s de intervalo: as tres fontes **estaveis** em linhas, chaves,
datas, marcas, agregados `Decimal` e fingerprints com `COLLATE "C"`.

### 32.2 Os tres backfills, uma tentativa cada

| Tabela | `run_id` | Janela | Apagadas | Publicadas | `EXCEPT` | Duracao |
| --- | --- | --- | --- | --- | --- | --- |
| `fact_ml_gestao_diaria` | `s2t3-full-ml` | 2025-04-27 .. 2026-08-12 | 1.621 | **1.629** | `(0,0)` | 8 s |
| `fact_tiktok_brand_content_daily` | `s2t3-full-brand` | 2025-10-05 .. 2026-08-12 | 1.551 | **1.556** | `(0,0)` | 6 s |
| `fact_tiktok_creator_daily` | `s2t3-full-creator` | 2025-10-07 .. 2026-08-12 | 185.035 | **185.697** | `(0,0)` | 55 s |

Antes de cada carga TikTok, o fingerprint da fonte foi reconferido contra o do precheck e
bateu — a fotografia carregada e' a mesma que foi validada.

### 32.3 Reconciliacao integral

Para as tres, comparacao independente do modulo, em conexoes separadas por engine: linhas,
chaves, datas, marcas, min/max, duplicidades, nulos obrigatorios, NaN e **todas** as somas
em `Decimal` (4, 21 e 6 colunas) conferem. As razoes conferem por contagem de nao-nulos.

A comparacao linha a linha foi feita **por conjunto**, imune a collation: zero tupla
exclusiva de qualquer lado, zero duplicata em qualquer lado. E' a lição do §29.5 aplicada
desde o inicio, em vez de descoberta no meio.

Invariantes do destino, nas tres: contem **somente** a janela publicada, **zero linha do
dia corrente ou posterior**, zero linha antes do inicio da fonte, zero marca fora da
allowlist, auditoria completa e **um unico `source_run_id`** por tabela.

### 32.4 Isolamento

Inventario completo de `marts` comparado antes e depois: das **34** tabelas, exatamente
**3** mudaram de contagem — as tres autorizadas. As **7** tabelas Shopee ficaram com
contagens identicas. Alembic permaneceu em `008`. Database: 197 MB -> 230 MB;
`creator_daily` ocupa 65 MB, `brand_content` 1000 kB e a fato do ML 616 kB.

Correcao factual: sao **7** tabelas Shopee em `marts`, nao 15. O numero anterior contava
indices, por nao filtrar `relkind='r'`.

### 32.5 `/operacoes` — paridade e prova runtime

**Paridade dos cinco blocos.** O SQL real do endpoint foi executado nas duas fontes, com o
mesmo teto D-1, e os dois payloads foram reconstruidos pelo **mesmo** codigo de
pos-processamento. Resultado: `alertas=0, ml_velocity=4, creators=30, lives=5, tk_daily=70`
nos dois lados e **zero divergencia campo a campo**, incluindo ordenacao, tipos e nulls.

**Prova runtime.** Com `datamart_engine = None` no processo — sem editar `.env` — e uma
Session real do Neon, `get_operacoes` atravessou inteiro: cinco blocos bem formados, as
cinco consultas passando pela Session, todas em `marts.*` e nenhuma citando `gold.`. O
payload runtime saiu **identico** ao do teste de paridade.

As cinco consultas foram verificadas uma a uma: teto D-1 presente, `_uses_datamart` = False,
e o conjunto de relacoes usadas e' exatamente as tres fatos de serving.

### 32.6 Correcao de redacao

Estava escrito que "a quantidade de dias nao mudou" ao descrever o teto D-1. Era falso e
foi corrigido em comentario, teste e nos dois documentos: os limites inferiores **nominais**
foram preservados (7, 14 e 30 dias), mas antes, por nao haver teto, a presenca do dia
corrente podia produzir **8, 15 ou 31 datas**. Agora as janelas tem exatamente **7, 14 e 30
dias fechados**. Retirar o dia corrente e' **alteracao comportamental intencional** — o
painel deixa de exibir um numero que mudava sozinho ao longo do dia —, nao uma troca neutra.

### 32.7 Estado e o que falta

O endpoint **ainda nao foi publicado**: o codigo esta validado localmente e aguarda
versionamento. **Airflow continua inexistente e nao comprovado** — a politica de backfill
historico periodico (semanal) permanece do futuro Airflow, sem DAG criada. Politica vigente:
**D-1 no fuso do Brasil + lookback incremental de 90 dias**, com piso de 7 e reprocessamento
pontual por `--date-from/--date-to`. **A decisao de frete no GMV TikTok permanece frente
separada e inalterada.**

## 33. Operacao SD2-A (17/08/2026) — tentativa de fechamento ate 16/08: `BLOCKED`

Objetivo era reconciliar as tres fatos com teto fixo em 16/08. A operacao parou
no precheck de cobertura das fontes, **antes de qualquer escrita**; as tres
autorizacoes (uma por tabela) nao foram consumidas.

Causa: `gold.tiktok_brand_daily` e `gold.tiktok_creator_daily` param em
**15/08**; so `gold.ml_gestao_diaria` alcanca 16/08. O diagnose read-only dos
proprios syncs confirmou de forma independente, recusando a janela com
`cobertura incompleta: 1 dia(s) sem linha` (exit 2) para brand e creator; o
diagnose do ML passou (exit 0).

Janelas apuradas no preflight, para reuso quando as fontes maturarem:

| tabela | `--date-from` (MIN real da fonte) | `--date-to` |
|---|---|---|
| `fact_ml_gestao_diaria` | 2025-04-27 | 2026-08-16 |
| `fact_tiktok_brand_content_daily` | 2025-10-05 | 2026-08-16 |
| `fact_tiktok_creator_daily` | 2025-10-07 | 2026-08-16 |

Integridade das fontes verificada e aprovada: fingerprint deterministico
identico em duas amostragens separadas, zero duplicidade de chave, zero NaN,
cobertura diaria contigua desde o MIN. Os `NULL` em `roas` (906 de 1.645 na
fonte ML) sao esperados — `roas` e' RATIO_COLUMN e esta fora de
`REQUIRED_COLUMNS`, que cobre apenas chave + aditivas.

Nada foi escrito: as tres tabelas seguem com 1.637 / 1.566 / 187.185 linhas,
`max(date) = 14/08` e os `source_run_id` de 14–15/08 intactos. Nenhum run_id
`sd2a-*` foi criado.

**Airflow continua inexistente e nao comprovado.** Nenhuma DAG, agendamento ou
deploy nesta operacao.

## 34. Operacao SD2-B (17/08/2026) — serving reconciliado: `SUCCESS`

A regra de corte passou a ser **independente por fonte**:
`effective_to = min(MAX(date) estavel da fonte, teto operacional)`. A regra
anterior — parar todas se qualquer fonte nao alcancasse o teto — era acoplada
demais e travava o ML por causa do atraso do TikTok. Com o corte independente,
as tres tabelas fecharam na mesma operacao.

Backfill historico integral, uma tentativa por tabela, todas bem-sucedidas:

| tabela | janela | apagadas | publicadas | run_id |
|---|---|---|---|---|
| `fact_ml_gestao_diaria` | 2025-04-27 → 2026-08-16 | 1.637 | 1.645 | `sd2b-full-ml-20260816` |
| `fact_tiktok_brand_content_daily` | 2025-10-05 → 2026-08-15 | 1.566 | 1.571 | `sd2b-full-brand-20260815` |
| `fact_tiktok_creator_daily` | 2025-10-07 → 2026-08-15 | 187.185 | 187.848 | `sd2b-full-creator-20260815` |

Nenhum `--backfill` foi usado: a janela e' explicita nas duas pontas, para que o
teto nao avance dinamicamente com o relogio.

O drift historico que motivava esta operacao foi **eliminado**. Alem do `EXCEPT`
bidirecional (0, 0) reportado pelos proprios syncs, uma reconciliacao
independente cross-database comparou conjunto de chaves e tupla de valores
coluna a coluna: **zero celula divergente** nas 5, 35 e 6 colunas de negocio,
contra 52, 532 e 267 celulas divergentes antes. Cada destino ficou com um unico
`source_run_id` cobrindo 100% das linhas — os run_ids `s2-*` de 14–15/08 foram
substituidos pelo DELETE+INSERT da janela completa.

Nota de interface: **o TikTok fecha em 15/08 e o ML em 16/08.** A diferenca e'
real (as fontes Gold do TikTok param em 15/08) e nao deve ser mascarada; a data
ausente nao foi fabricada.

Politica vigente inalterada: D-1 no fuso do Brasil + lookback incremental de 90
dias, piso de 7, reprocessamento pontual por `--date-from/--date-to`. **Airflow
continua inexistente e nao comprovado**; nenhuma DAG criada nesta operacao.

## 35. Gate S2 — FECHADO: `PASS COM RESTRICAO` (17/08/2026)

`/operacoes` esta em producao lendo o Neon. O Gate S2 encerra aqui.

**Tres coisas distintas aconteceram, e confundi-las produz relatorio falso:**

1. **Codigo versionado** — a troca das cinco consultas de `gold.*` para `marts.*`, com
   teto D-1 em `America/Sao_Paulo`, foi versionada em `861648a` e esta contida em
   `41eb1719a2730f545aaebd038c616bf0d0746ff7` (os commits posteriores tocaram apenas
   `docs/` e `pipelines/`; a arvore de `apps/api` e' byte-identica nos dois).
2. **Dados publicados no Neon** — as tres fatos de serving foram carregadas e
   reconciliadas por execucoes manuais dos CLIs versionados.
3. **Deploy do backend** — executado **manualmente pelo proprietario no painel do
   Render**, nao pelo agente. Esta sessao nunca teve acesso executavel ao Render:
   sem CLI, sem token, sem variavel de ambiente.

### 35.1 O que mudou em producao

| Endpoint | Antes do deploy | Depois |
| --- | --- | --- |
| `/api/v1/performance/operacoes` | **500** em ~10,4 s | **200** em **0,50 s** e **0,44 s** |
| `/health` | 200 | 200 |
| `/api/v1/performance/health-datasource` | 200 | 200, `active_source=neon_marts`, `db_connected=true` |

A queda de ~10,4 s para 0,44 s e' a evidencia direta de que o endpoint **deixou de
esperar o Data Mart**: os 10 s eram o timeout da tentativa de conexao a um host que o
Render nao alcanca.

Payload **deterministico** nas duas leituras (11.037 bytes, byte-identico) e **identico
ao snapshot reconciliado no Neon** — nove agregados conferidos, zero divergencia. Cinco
blocos: `alertas=0`, `ml_velocity=4`, `creators=30`, `lives=5`, `tk_daily=70`. Campos,
tipos, nulls e as tres ordenacoes preservados. **Zero dado de 17/08** no payload.

Endpoints previamente saudaveis sem regressao: `overview`, `daily`, `trend`, `canais` e
`quality`, todos 200.

### 35.2 A restricao: cobertura das fontes

| Fato de serving | Cobertura |
| --- | --- |
| `marts.fact_ml_gestao_diaria` | ate **16/08** |
| `marts.fact_tiktok_brand_content_daily` | ate **16/08** |
| `marts.fact_tiktok_creator_daily` | ate **15/08** |

A `creator_daily` para em 15/08 porque **a fonte Gold para em 15/08**. O dia 16/08 e'
**ausencia da fonte, nunca zero fabricado**: nao existe linha, e o bloco `creators`
agrega seis dias reais (10/08..15/08) em vez de sete. A consequencia a comunicar a quem
le o painel e' que esse bloco cobre um dia menos que `ml_velocity` e `tk_daily` — e' a
verdade da fonte, mas e' assimetria invisivel na tela.

### 35.3 O delta pos-snapshot do ML

Depois de a sincronizacao terminar, a Gold **reafirmou uma chave** do ML em **14/08**:

| | Marts (publicado) | Gold (posterior) | Delta |
| --- | --- | --- | --- |
| `gmv` | R$ 27.805,78 | R$ 27.750,78 | **−R$ 55,00** |
| `paid_orders` | 286 | 285 | **−1** |

Impacto na janela de sete dias: **0,0060%** do GMV e **0,0085%** dos pedidos.

Classificacao: **alteracao legitima da fonte, posterior ao snapshot reconciliado**. A
fotografia publicada permanece consistente com o snapshot produzido as 13:28:44 UTC, e o
delta **sera absorvido pelo proximo incremental**.

**Isto nao estabelece tolerancia permanente**, nao cria regra de qualidade nova e nao
autoriza ignorar outras divergencias. Foi aceitacao explicita e pontual, para este
release, verificada como sendo exatamente essa chave e essas duas colunas — nenhuma
outra data, nenhuma outra coluna, e zero divergencia nas tres superficies TikTok.

### 35.4 A igualdade e' relativa ao snapshot, nao absoluta

O criterio deste release foi deliberadamente reformulado, e a razao e' estrutural: **a
Gold e' fonte viva**. Ela reafirma dias ja fechados por cancelamento, devolucao ou ajuste
retroativo, e continua mudando depois da carga. Exigir igualdade absoluta com uma fonte
que se move torna qualquer release impossivel por construcao — sempre havera uma chave
nova divergindo entre a carga e a verificacao.

O criterio que substitui a igualdade absoluta:

1. destino reconciliado integralmente **com o snapshot usado pela execucao**;
2. zero corrupcao, duplicidade, nulo obrigatorio ou NaN;
3. divergencias posteriores ao snapshot **identificadas e declaradas**;
4. nenhuma divergencia nova alem da unica chave autorizada;
5. codigo comprovadamente independente do Data Mart.

### 35.5 O que NAO foi resolvido

**O Gate G4 nao foi fechado.** Apenas **uma** das quatro superficies saiu do 500:

| Rota | Estado |
| --- | --- |
| `/operacoes` | **200 pelo Neon** |
| `/inteligencia` | **500** em ~10,8 s |
| `/tempo-real` | **500** em ~10,8 s |
| `/brand-detail` | **500** em ~10,8 s |

As tres continuam dependentes do Data Mart e sao escopo do **Gate S3, ainda nao
iniciado**. Nao afirmar que o sistema esta independente do Data Mart: o que se tornou
independente foi `/operacoes`.

**Smoke visual nao executado.** Chrome esta instalado, mas sem driver de automacao
(`playwright` e `selenium` ausentes) e nenhuma dependencia foi instalada. A pagina HTTP
`/operacoes` no dominio canonico respondeu 200 em 1,11 s sem mensagem de falha no HTML
servido, mas o Next.js renderiza no cliente: isso **nao substitui QA visual** e nao prova
que os cinco blocos apareceram na tela.

**Airflow continua inexistente e nao comprovado.** Nenhuma DAG, connection, secret ou
pool. §24 permanece a referencia.

### 35.6 Dividas e pendencias, em ordem

1. **Os syncs de serving nao estao no `full_daily` nem em Airflow.**
   `sync_ml_gestao_diaria` e `sync_tiktok_serving` nao aparecem no orquestrador — seus
   steps sao `daily_ml`, `daily_tiktok`, `gold_regional_incremental`,
   `sync_region_if_needed`, `sync_produtos_ml/tiktok` e `health_check`. Portanto o
   serving **de producao** volta a ficar defasado sem execucao manual. Antes do deploy
   isso era problema de laboratorio; agora e' do usuario.
2. **O `full_daily` de 17/08 terminou com `LastTaskResult=1`** — causa ainda nao
   investigada.
3. **Gate S3 (futuro, nao iniciado):** migrar `/inteligencia`, `/tempo-real` e
   `/brand-detail`, removendo a dependencia operacional dessas rotas do Data Mart.
4. **Representacao de `total_gmv` quando Shopee esta `NULL`** — o total pode parecer
   completo tratando canal indisponivel como zero ou omissao. Problema de verdade, alheio
   a `/operacoes`.
5. **Publicar shop-stats Shopee valido de 16/08** quando disponivel.
6. **Smoke visual de `/operacoes`** pendente.
7. **Airflow** ainda nao existe, nao esta configurado e nunca foi executado neste projeto.

## 36. Checkpoint O1 Task 2/2 — ponte de serving IMPLEMENTADA, ainda NAO executada (17/08/2026)

O diagnostico da Task 1/2 provou por que o serving nao se atualiza sozinho: os tres CLIs
calculam a janela com teto FIXO em D-1, e `gold.tiktok_creator_daily` estava em D-2. Nesse
estado, `date_coverage` reprova a janela por dia faltante e o CLI sai com codigo nao-zero
**sem escrever nada** — seguro, mas inviavel para execucao diaria.

Esta task implementou a ponte. **Nada foi executado**: nenhum sync rodou, nenhum banco foi
escrito, nenhum agendamento foi criado ou alterado, nenhum deploy aconteceu. O codigo
existe e esta testado; a operacao continua exatamente como estava antes desta task.

### 36.1 O contrato de watermark

Por tabela, independentemente:

```
effective_date_to = min(D-1 em America/Sao_Paulo, source_max)
date_from         = max(source_min, effective_date_to - (lookback_days - 1))
```

`source_max` vem de um `MAX(<coluna de data>)` na propria fonte, com a **mesma allowlist de
marca** que o CLI usa na leitura real. Isso nao e' detalhe: se `gold.tiktok_brand_daily`
tivesse 16/08 para uma marca FORA da allowlist e apenas 15/08 dentro dela, um `source_max`
sem filtro pediria uma janela que o CLI nao consegue cobrir — e a cobertura reprovaria,
reproduzindo a falha que a ponte existe para evitar. A allowlist vai como **parametro**
(`brand = ANY(%(brands)s)`), nunca interpolada, e e' a mesma tupla do conector.

Consequencias, cada uma travada por teste:

| Regra | Como e' garantida |
| --- | --- |
| ML e brand nao sao rebaixados por creator em D-2 | janela resolvida por target; nao existe watermark comum |
| D0 nunca e' publicado | teto D-1 no wrapper **e** `validate_window` no CLI — duas barreiras |
| dia ausente permanece ausente | o wrapper nao escreve nada e nao pede dia que a fonte nao tem |
| `source_max` nulo ou anterior ao `source_min` | falha **antes** de qualquer subprocesso |
| lookback < 7 | falha antes de abrir conexao |
| `--table all` | nunca usado: uma invocacao por target, com `--table` explicito |
| zero retry | exatamente um subprocesso por invocacao, sem laco nem backoff |
| sem shell injection | `subprocess.run` recebe LISTA; `shell=True` nao existe no modulo |
| exit code | propagado do filho sem traducao (ML sai 1, TikTok sai 2) |

### 36.2 Criticidade: `critical=True`, contrariando a recomendacao inicial

O diagnostico sugeriu `critical=False`. A decisao do Checkpoint O1 foi o oposto, e a razao
e' que `/operacoes` **ja esta em producao** lendo essas tabelas: defasagem tem que aparecer
como FAILED/exit 1, nao como `DEGRADED` silencioso.

Isso e' seguro porque a falha de um serving **nao desfaz** o que ja foi commitado. Cada
carga anterior tem transacao propria, e cada sync de serving e' atomico — o endpoint
continua servindo o snapshot anterior em vez de ficar sem dado. Marcar como nao-critico
esconderia do usuario exatamente aquilo que ele veria no painel.

### 36.3 Fiacao no `full_daily`

Tres steps novos, depois de toda a ingestao e antes do `health_check`:

| Step | Depende de | Timeout do step | Timeout do filho |
| --- | --- | --- | --- |
| `serving_ml` | `daily_ml` | 600s | 540s |
| `serving_tiktok_brand` | `daily_tiktok` | 600s | 540s |
| `serving_tiktok_creator` | `daily_tiktok` | 1800s | 1740s |

Nao ha' dependencia cruzada de proposito: uma fonte de canal bloqueada impede **somente** o
serving daquele canal. TikTok fora do ar nao congela o serving do ML.

O creator recebe o triplo porque reescreve **66.347 linhas** numa janela de 90 dias, contra
360 do ML e 450 do brand (medido em 17/08). O timeout do filho fica **abaixo** do timeout do
step para que o wrapper ainda consiga reportar — sem isso o orquestrador mataria o wrapper e
deixaria o CLI orfao escrevendo no Neon.

Orcamento interno do `full_daily`: **3600s -> 6600s**, contra 9000s do lock externo (margem
de 2400s, ~36%). `SERVING_REFRESH_STEP_TIMEOUT_BUDGET_SECONDS` = 3000s.

### 36.4 TaskKey manual `serving_refresh` — contingencia, nao automacao

Pipeline com **somente** os tres steps de serving, na ordem ML -> brand -> creator.
**Nao esta agendada**, nao tem entrada em `PROPOSED_SCHEDULE` e **nenhuma tarefa do Windows
foi criada ou alterada** por esta task. Serve a um cenario real: o `full_daily` das 06:00 foi
bloqueado no preflight porque a VPN estava fora, e mais tarde o operador quer atualizar so'
o serving, sem reprocessar daily/produtos/regional.

Reusa o **lock logico do `full_daily`** (`Lock = "full_daily"` em `Get-TaskDefinitions`):
sobreposicao com uma execucao agendada em andamento e' impossivel por construcao, nao por
disciplina do operador. E' o oposto deliberado de `shopee_manual_refresh`, que tem lock
proprio porque mexe em fontes disjuntas.

Sem `health_check`: ele reprova por fontes **alheias** ao serving — em 17/08 saiu com exit 1
apenas porque `ml_produto_ranking` estava a 39,8h do limite de 30h. Incluir aqui faria uma
recuperacao de serving bem-sucedida reportar FAILED por um motivo que ela nao pode resolver.

### 36.5 Preflight

As tres fontes novas (`serving_ml`, `serving_tiktok_brand`, `serving_tiktok_creator`) exigem
Data Mart (fonte), Neon (destino) e a existencia das tres tabelas de serving.

Sobre a checagem de migration: o pedido era verificar Alembic 008. A implementacao verifica
a **existencia das tres tabelas** via `to_regclass`, reportando a revisao corrente do Alembic
no detalhe sem usa-la como critério. Os dois provariam a mesma coisa hoje, mas comparar com o
literal `'008'` passaria a bloquear todo o serving no dia em que uma migration 009 de qualquer
outro assunto entrasse — a pre-condicao real do sync e' a tabela existir, nao o numero da
revisao. Nenhuma linha das fatos e' lida: `to_regclass` nao toca em conteudo.

### 36.6 O que esta ponte NAO resolve

**A ponte nao foi executada.** Afirmar que o serving "agora se atualiza em producao" seria
falso: o codigo existe, esta testado e nao rodou nenhuma vez.

**Nenhum Scheduler foi criado ou alterado.** `mktplace_full_daily` continua sendo a unica
tarefa do sistema, com a mesma acao, o mesmo horario e as mesmas configuracoes. A partir da
proxima execucao dela, os tres steps novos passariam a ser exercitados — e e' exatamente por
isso que um piloto autorizado deve vir antes de confiar no resultado.

**Airflow nao existe**, nao esta configurado e nunca executou este projeto. §24 permanece a
referencia; esta ponte e' temporaria por construcao.

**A confiabilidade continua dependendo de notebook ligado, usuario logado e VPN ativa.** A
tarefa roda com `LogonType=Interactive` e `RunLevel=Limited`. Em agosto, execucoes as 06:00
apareceram em apenas 5 dos 17 dias.

**O horario 06:00 continua uma divida**, e a mais impactante: e' justamente quando a VPN
tende a estar fora. Nao foi alterado nesta task.

**Backfill historico periodico ainda requer decisao futura.** O lookback de 90 dias absorve
reafirmacoes recentes, mas nao cobre correcao anterior a essa janela. A cadencia de
`--backfill` (semanal? mensal?) nao foi decidida nem implementada.

**Risco residual conhecido, nao corrigido:** se o orquestrador matar o wrapper por timeout do
step, no Windows o CLI neto pode sobreviver como orfao. O timeout do filho, 60s abaixo do
timeout do step, existe para que o caminho normal nunca chegue la'. Nao ha' arvore de
processos gerenciada.

### 36.7 Estado desta task

`IMPLEMENTADO E TESTADO, NAO EXECUTADO`. A Task 2/2 so' sera' operacionalmente concluida
apos um **piloto autorizado**: primeiro o wrapper em modo diagnostico (sem `--apply`)
conferindo `effective_date_to` contra os watermarks reais, depois uma execucao manual
completa com VPN ativa, e so' entao confiar no agendamento.

## 37. Checkpoint O1 — piloto da ponte diaria executado UMA vez: `OK` (17/08/2026)

O codigo da secao 36 foi versionado em `950c40747c4f038f0c6e62f06903d1bf22aedfb2` (11
arquivos) e, depois disso, a TaskKey manual foi executada **exatamente uma vez**:

```
powershell -NoProfile -NonInteractive -File scripts\run_task.ps1 -TaskKey serving_refresh
```

Uma unica invocacao, tres tentativas independentes de escrita, **zero retry**. Nenhuma
execucao individual com `--apply`, nenhum `full_daily`, nenhum backfill completo.

### 37.1 Resultado

Inicio 12:33:16, fim 12:34:21 — **65,1 s**. `STATUS=SUCCESS EXITCODE=0`, lock
`full_daily.lock` adquirido no inicio e **liberado** no fim. `STATUS GERAL: OK`.

| Step | run_id | Janela | Apagadas | Publicadas | EXCEPT | Exit |
| --- | --- | --- | --- | --- | --- | --- |
| `serving_ml` | `serving_ml_20260817_123326` | 19/05..16/08 | 360 | 360 | (0,0) | 0 |
| `serving_tiktok_brand` | `serving_brand_20260817_123340` | 19/05..16/08 | 450 | 450 | (0,0) | 0 |
| `serving_tiktok_creator` | `serving_creator_20260817_123353` | 19/05..16/08 | 65.574 | **66.223** | (0,0) | 0 |

O creator publicou 649 linhas a mais do que apagou: e' o dia **16/08**, que a fonte passou a
ter. O preflight dos tres steps aprovou (Data Mart, Neon e as 3 tabelas existindo, revisao
008) antes de cada invocacao.

### 37.2 Watermarks efetivos — e uma mudanca relevante da fonte

| Target | `source_max` | D-1 | `effective_date_to` | `date_from` | Datas |
| --- | --- | --- | --- | --- | --- |
| ml | **17/08 (D0)** | 16/08 | **16/08** — limitado por D-1 | 19/05 | 90/90 |
| brand | 16/08 | 16/08 | **16/08** — igual a D-1 | 19/05 | 90/90 |
| creator | **16/08** | 16/08 | **16/08** — igual a D-1 | 19/05 | 90/90 |

`gold.ml_gestao_diaria` tinha D0 e foi corretamente cortada em D-1 — o contrato de teto
funcionou contra dado real, nao so' em teste.

**Registro honesto de cobertura:** a Gold do creator, que na Task 1/2 estava em D-2 (15/08),
avancou para 16/08 antes do piloto. Portanto **este piloto NAO exercitou o caminho "creator
atrasado"**, que e' justamente a razao de existir da ponte. Os tres targets estavam em D-1, e
o comportamento de `min(D-1, source_max)` com fonte atrasada segue provado apenas por teste,
nao por execucao real. Isso e' pendencia de observacao, nao defeito.

### 37.3 Reconciliacao

Nas tres janelas, Gold x Marts: mesma contagem, mesmas chaves, mesmas datas, `EXCEPT`
bidirecional zero nas duas direcoes, somas `Decimal` identicas em todas as colunas aditivas
verificadas, zero duplicidade, zero nulo obrigatorio, zero NaN.

Fingerprints `MD5` calculados com `COLLATE "C"` explicito nas duas pontas (as duas usam
locales diferentes; sem isso o fingerprint divergiria com dados identicos):

| Target | Fingerprint | Destino == snapshot da execucao | Gold agora |
| --- | --- | --- | --- |
| ml | `6c2a5cbd9f0b` | sim | tambem identica |
| brand | `aeadbebe5977` | sim | tambem identica |
| creator | `178790eb3deb` | sim | tambem identica |

**Zero delta pos-snapshot** nesta rodada — as tres fontes estavam identicas ao snapshot
depois da escrita, algo que nao se pode esperar sempre de uma Gold viva.

Antes da escrita houve amostragem dupla das tres fontes separada por **72,5 s**, com
fingerprint, contagens e agregados `Decimal` identicos nas duas leituras: a fonte estava
estavel durante a operacao.

### 37.4 Isolamento provado

Snapshot das **35 tabelas** de `marts` antes e depois. Uma unica contagem mudou:
`fact_tiktok_creator_daily` (187.917 -> 188.566). As outras 34 ficaram estaveis, incluindo
`fact_ml_gestao_diaria` e `fact_tiktok_brand_content_daily` — que apagaram e publicaram o
mesmo numero de linhas, logo contagem estavel **nao** prova ausencia de mudanca; o que prova
e' o `source_run_id` novo, verificado em todas as tres.

`alembic_version` inalterado em **008**. Zero Shopee, zero regional, zero produtos, zero
daily. Nenhuma tabela fora das tres autorizadas mudou de contagem.

Os 773 registros do creator que mantiveram o `run_id` anterior sao de **18/05**, fora da
janela de 90 dias — prova de que o `DELETE` respeitou o escopo pedido.

### 37.5 `/operacoes`

| Verificacao | Resultado |
| --- | --- |
| local, com `datamart_engine = None` | 200 em 4,48 s, os 5 blocos presentes |
| producao `/health` | 200 |
| producao `/health-datasource` | 200, `active_source=neon_marts`, `db_connected=true` |
| producao `/operacoes` | **200 em 0,45 s**, 11.055 bytes |
| payload producao x Neon atualizado | **byte-identico** |
| dado de D0 (17/08) no payload | **zero**, nas duas pontas |

Blocos: `alertas=0`, `ml_velocity=4`, `creators=30`, `lives=5`, `tk_daily=70`. O bloco
`creators` passou a cobrir sete dias reais — a assimetria registrada na secao 35 se resolveu
porque a fonte avancou, nao porque algo foi fabricado. Sem regressao em `overview`, `trend`,
`canais`, `quality` e `daily` (este ultimo com os parametros que exige).

Nenhum deploy foi feito: o backend em producao ja rodava a revisao que le `marts.*`, e o que
mudou foi o **dado**.

### 37.6 O que segue aberto

**A `serving_refresh` esta operacional e comprovada.** Ja o `full_daily` **contem** os tres
steps novos, mas **a primeira execucao agendada ainda nao foi observada** — o piloto exercitou
a TaskKey manual, nao o caminho automatico com as dependencias `daily_ml`/`daily_tiktok`.

**Nenhuma alteracao do Scheduler.** `mktplace_full_daily` segue `Ready`, habilitada, mesma
acao e mesmo horario, `LastTaskResult=1` da execucao de 17/08 as 06:00, proxima em 18/08 as
06:00. Nada foi criado, desabilitado ou reconfigurado.

**A confiabilidade continua dependendo de notebook ligado, usuario logado e VPN ativa**
(`LogonType=Interactive`, `RunLevel=Limited`).

**O horario 06:00 permanece divida** — e' quando a VPN tende a estar fora; em agosto so' 5 dos
17 dias tiveram execucao as 06:00.

**Airflow nao existe**, nao esta configurado e nunca executou este projeto.

**Backfill historico periodico segue pendente:** o lookback de 90 dias nao cobre correcao
anterior a essa janela, e a cadencia nao foi decidida.

**Proxima etapa:** observar UMA execucao agendada do `full_daily`, sem retry, e comparar o
resultado com este piloto.

## 38. Gate S3 Task 2/3 — `/inteligencia` e `/brand-detail` implementadas, NADA aplicado (18/08/2026)

Esta task escreveu codigo, migrations e testes **dentro de um git worktree isolado**
(worktree isolado do S3, detached em `d04306e` — e portanto **um commit atras** de
`origin/main`, que avancou para `a1c5ffe` em 18/08 com o hotfix do help regional; a
incorporacao e' escopo da Task 3, ver §38.12). O **checkout operacional usado pelo
Task Scheduler** **nao recebeu um byte** — ele roda diariamente
sob o Task Scheduler e precisava continuar identico para a execucao de 19/08 as 06:00.

**Nada foi aplicado:** zero migration executada, zero escrita em banco, zero sync com
`--apply`, zero backfill, zero alteracao de Scheduler, zero commit, push ou deploy.

### 38.1 Contexto herdado do O1

O Checkpoint O1 encerrou como `PASS COM RESTRICAO`. A execucao agendada de 18/08 as
06:00 comprovou o caminho automatico: dez steps `SUCCESS`, `LastTaskResult=0`,
`STATUS GERAL: OK`, `ok_critical=true`, lock liberado. O aceite central foi a fonte
Creator em **D-2**: `source_max=16/08` contra D-1 de 17/08, `effective_date_to`
limitado honestamente em **16/08**, 66.224 linhas publicadas, `EXCEPT` bidirecional
`(0,0)`, e **zero linha fabricada em 17/08** — ausencia representada como ausencia.

A restricao do O1 era o ranking ML. A investigacao fechou a questao: a "queda de
1.766 para 1.648" **nunca existiu** — era comparacao entre `COUNT(*)` bruto da Gold
(que tem 119 chaves duplicadas) e um destino deduplicado por `(brand, item_id)`.
Chaves, distribuicao por marca e `ad_spend` em paridade perfeita; a divergencia
residual de 118 chaves em `gross_revenue`/`units_sold`/`unique_buyers` e' **restatement
posterior ao snapshot**, com `ad_spend` e `ad_roas` em divergencia zero.

O que faltava era **provar** essa consistencia, e nao era possivel: `sync_ml`
declarava "full refresh" mas escrevia com `ON CONFLICT DO UPDATE`, sem preservar
fotografia alguma. A Parte D desta task corrige isso.

### 38.2 Reuso: quatro das sete fontes ja estavam no Neon

| Fonte Gold | Destino | Cobertura | Estrategia |
| --- | --- | --- | --- |
| `gold.tiktok_brand_daily` | `fact_tiktok_brand_content_daily` (007) | 36/36 | janela 90d (O1) |
| `gold.tiktok_creator_daily` | `fact_tiktok_creator_daily` (008) | 6/6 | janela 90d (O1) |
| `gold.ml_produto_ranking` | `fact_ml_produto_ranking` | 15/15 | **snapshot** (Parte D) |
| `gold.tiktok_product_daily` | `fact_tiktok_product_daily` | 10/12 | upsert + 2 colunas (Parte E) |
| `gold.ml_cross_company_summary` | **nova** (009) | 0/9 | **snapshot** (Parte C) |
| `gold.v_channel_efficiency` | **nova** (010) | 0/7 | **snapshot** (Parte C) |
| `gold.tiktok_shop_hourly` | — | — | **fora do S3** |

A migration 006 (`fact_ml_gestao_diaria`) nao serve nenhuma das duas rotas.

### 38.3 Por que snapshot, e por que NAO no wrapper do O1

`serving_refresh.Target` tem **12 campos obrigatorios, zero defaults**, incluindo
`date_column` e `source_min_date`, e `build_argv` **sempre** emite
`--date-from/--date-to`. As duas fontes novas nao cabem:

- `ml_cross_company_summary` e' snapshot **sem coluna de data** — quatro linhas, uma
  por marca ML. Fabricar uma data mentiria sobre o grao;
- `v_channel_efficiency` TEM data, mas `/brand-detail` aceita **qualquer mes** desde
  outubro/2025, e a medicao mostrou **71,4% das linhas fora da janela de 90 dias**
  (3.378 de 4.728). Uma janela movel congelaria esse historico. A leitura integral
  custou **1,25 s e 2,45 s** em duas medicoes separadas por 35 s, com fingerprint
  identico (`c66a1b67...`). Substituir tudo e' mais simples E mais correto.

Generalizar o wrapper foi descartado: seria um modulo tentando servir contratos
incompativeis, e e' gatilho explicito de stop-loss. O modulo novo,
`pipelines/sync_serving_snapshots.py`, tem **allowlist literal de dois targets**, sem
registro dinamico e sem framework.

Contrato dos dois: fonte read-only capturada **uma vez**; validacao de chaves, tipos,
duplicidade e volume (piso absoluto e proporcional ao destino); advisory lock proprio
(909120009 e 910120010); staging `pg_temp ... ON COMMIT DROP`; `DELETE` integral;
`INSERT` de colunas explicitas; reconciliacao contra a **fotografia** por contagem,
chaves, agregados `Decimal`, `EXCEPT` bidirecional e fingerprint; commit so' no fim;
rollback integral em qualquer falha. Zero retry, zero backoff, zero sleep.

O fingerprint e' calculado **em Python**, com `hashlib`. Nao em SQL de proposito:
`MD5(STRING_AGG(... ORDER BY texto))` depende de colacao, e as duas pontas usam
locales diferentes (`en_US.UTF-8` no RDS, `C.UTF-8` no Neon) — o mesmo dado geraria
hashes distintos.

### 38.4 O `sync_ml` virou snapshot transacional de verdade

Antes: `ON CONFLICT DO UPDATE`. Chave que desaparecia da fonte **permanecia no destino
para sempre**, e nenhuma fotografia era preservada — impossivel provar consistencia.

Agora: advisory lock proprio (911120011), staging `pg_temp`, `DELETE` integral,
`INSERT`, reconciliacao contra a fotografia capturada, commit so' no fim, rollback
integral. `refreshed_at` recebe **o mesmo instante** para todas as linhas do snapshot —
antes o `INSERT` usava `now` e o `DO UPDATE` usava `NOW()`, e linhas do mesmo refresh
carregavam instantes diferentes.

Preservado sem alteracao: dedup `DISTINCT ON (brand, item_id) ORDER BY ...,
gross_revenue DESC NULLS LAST`, allowlist de marcas, as 24 colunas, a guarda
`MIN_ROWS_RATIO`, o contrato de CLI, a forma do retorno (`{"source": N, "upserted": N}`)
e a auditoria. `sync_shopee` e `sync_tiktok` continuam com upsert de proposito.

### 38.5 Migrations 009, 010 e 011 — escritas, NAO aplicadas

Cadeia linear `008 -> 009 -> 010 -> 011`, head unico:

| Revisao | Objeto | Grao / PK |
| --- | --- | --- |
| **009** | `marts.fact_ml_cross_company_summary` | `(brand)`, 4 linhas, sem indice extra |
| **010** | `marts.fact_tiktok_channel_efficiency_daily` | `(date, brand, channel)` + indice `(brand, date)` |
| **011** | `ALTER` em `marts.fact_tiktok_product_daily` | `+active_videos`, `+video_views` |

`CREATE TABLE` sem `IF NOT EXISTS` (colisao tem de falhar alto); `NUMERIC` sem escala
(escala arredondaria e quebraria a igualdade de payload); `CHECK >= 0` validado contra
os minimos medidos na fonte; `CHECK <> 'NaN'` explicito, porque `'NaN'::numeric >= 0`
avalia TRUE em Postgres.

As duas colunas da 011 nascem **anulaveis e sem default**. `NOT NULL` falharia sobre as
213 mil linhas existentes, e `DEFAULT 0` apagaria a distincao entre "nao
retroalimentado" e "zero medido" — distincao real: na fonte as duas colunas tem **zero
nulo** e ~104.800 zeros cada.

### 38.6 Wiring: 12 steps no `full_daily`

| # | Step novo | Depende de | Timeout |
| --- | --- | --- | --- |
| 10 | `serving_ml_cross_company` | `sync_produtos_ml` | 300s |
| 11 | `serving_tiktok_channel_efficiency` | `sync_produtos_tiktok`, `serving_tiktok_brand`, `serving_tiktok_creator` | 600s |

Ambos `critical=True`: as tabelas alimentam telas que vao a producao, e defasagem tem
de aparecer como FAILED. A falha nao apaga dado anterior — o sync substitui a tabela
DENTRO de uma transacao.

`serving_ml_cross_company` depende de `sync_produtos_ml` porque `/inteligencia` le os
**dois** snapshots ML na mesma tela; publicar um sem o outro serviria metades de
instantes diferentes. As datas das quatro fontes de `/brand-detail` **nao** sao
niveladas: brand e creator mantem `min(D-1, source_max)`, e a channel efficiency e'
snapshot integral. A dependencia e' de **ordem de execucao**, nao de janela.

Orcamento interno: **6.600s -> 7.500s**, contra 9.000s do lock externo (margem de
1.500s, 20%). `health_check` segue por ultimo e `always_run=True`. Zero Shopee.
Nenhuma tarefa do Windows criada ou alterada.

### 38.7 Troca do backend

`/inteligencia`: as **sete** consultas passaram para `marts.fact_ml_produto_ranking`
(5), `marts.fact_ml_cross_company_summary` (1) e `marts.fact_tiktok_product_daily` (1).
Alem disso, `date.today()` deu lugar a `_hoje_operacional()`, que resolve o dia em
`America/Sao_Paulo` via `zoneinfo` — biblioteca padrao, zero dependencia nova. Num
servidor UTC o dia virava as 21:00 locais e a janela de 30 dias de `tk_products` saia
deslocada.

`/brand-detail`: as **cinco** consultas passaram para as quatro fatos `marts.*`.

Zero `gold.`, zero `raw.` e `_uses_datamart=False` nas duas funcoes. A troca foi
cirurgica: `gold.tiktok_product_daily` aparece em outras funcoes do arquivo e **nao**
foi tocada la'. As definicoes duplicadas nao relacionadas (`get_canais` 3x,
`get_quality` 3x) **nao** foram refatoradas — mexer nelas ampliaria o diff sem pedido.

**Payload preservado integralmente.** Os dois contratos congelados foram escritos
ANTES da troca, rodaram verdes contra `gold.*`, e continuam verdes contra `marts.*`
**sem uma unica expectativa editada** — 168 testes. Nenhum campo novo, nenhum campo
removido, nenhum `response_model` criado, zero frontend.

### 38.8 Frescor: por preflight, nao por payload

A auditoria da Task 1/3 sugeriu expor `refreshed_at`/`source_max` na interface e ao
mesmo tempo prometeu payload identico — as duas coisas sao incompativeis, e nenhum dos
contratos atuais tem campo de frescor. O padrao adotado:

- payload **integralmente preservado**, zero campo novo, zero frontend;
- frescor controlado por **preflight, logs, `audit.source_sync_run`,
  `synced_at`/`source_run_id` por linha e bloqueio de publicacao**;
- evolucao visual de frescor e' **frente separada**.

Consequencia aceita e declarada: quem olha o painel **nao ve** que o dado esta
atrasado. A protecao e' operacional — o sync bloqueia a publicacao em vez de servir
dado errado — nao visual.

### 38.9 O que a Task 3/3 tera de fazer

**Antes do cutover de `/brand-detail`**, reconciliar integralmente as **quatro**
dependencias, de **outubro/2025 ate o ultimo dia disponivel**:
`fact_tiktok_brand_content_daily`, `fact_tiktok_creator_daily`,
`fact_tiktok_product_daily` e a nova `fact_tiktok_channel_efficiency_daily`.

A reconciliacao tera de cobrir: igualdade de chaves; igualdade de valores; corte por
**mes x marca** em todo o intervalo; duplicidades; nulls; NaN; fingerprint sob
`COLLATE "C"`; **zero null em `active_videos` e `video_views`** apos o backfill
integral; e nenhuma chave exclusiva em qualquer direcao.

A divida de restatement historico e' real e medida neste repositorio (§30.3):
`new_videos_posted` divergiu **+30 (0,0042%)** num corte comum, e o documento conclui
que numero exibido que muda conforme a fonte e' material por definicao. **O lookback de
90 dias nao garante convergencia futura do historico** — um restatement em novembro de
2025 nunca sera absorvido por incremental. Backfill periodico permanece necessario, e
sua cadencia segue **pendente de decisao**.

**Antes do cutover de `/inteligencia`**, executar os **dois** snapshots ML na mesma
rodada operacional e provar cada destino contra a fotografia capturada pelo respectivo
sync — nunca contra uma releitura posterior da Gold, que e' mutavel e sem dimensao
temporal.

### 38.10 O que NAO aconteceu nesta task

- nenhuma migration aplicada; `alembic` segue em **008** no banco real;
- nenhuma escrita em banco, nenhum sync com `--apply`, nenhum backfill;
- `--full` do TikTok **nao** foi executado;
- nenhuma alteracao de Scheduler; a tarefa segue habilitada, com proxima execucao em
  19/08 as 06:00;
- **Airflow nao e' necessario neste gate** e nao foi tocado;
- `/inteligencia` e `/brand-detail` **nao foram publicadas** — seguem 500 em producao;
- `/tempo-real` permanece **fora do S3**, por exigir serving intraday e decisao de
  produto;
- nenhum commit, push ou deploy;
- o **checkout operacional permaneceu byte a byte intacto**.

### 38.11 Correcao de observabilidade: o health check passou a monitorar as duas fontes

A revisao da Task 2/3 apontou uma lacuna material: os dois steps novos entraram no
`full_daily` como `critical=True`, mas `pipelines/ops/health_check.py` **nao os
monitorava**. Nesse estado, um sync podia falhar — ou nem executar — por dias, e o
health check ainda reportaria `ok_critical=true`, deixando `/inteligencia` e
`/brand-detail` defasados **em silencio**.

Fechada de forma estreita, sem redesenhar o health check:

**Auditoria.** `pipelines/sync_serving_snapshots.py` nao registrava nada em
`audit.source_sync_run` — os syncs de serving do O1 tambem nao registram, entao
estes dois sao os primeiros. Foi adicionada a instrumentacao minima, no padrao ja
usado por `sync_produtos.py`/`daily_performance.py`/`sync_region_daily.py`: um
`INSERT 'running'` no inicio e um `UPDATE` com status no fim, numa conexao
**separada** da transacao de dados — o registro `failed` precisa sobreviver ao
rollback, senao a falha apagaria a propria evidencia. Somente `--apply` registra;
diagnostico nao aparece no audit log como publicacao. Uma execucao logica por
target: um par start/finish, nunca duplicado. Mensagem de erro **sanitizada** e
truncada em 500 caracteres. Zero retry, zero dependencia nova.

**Nomes.** Uma unica string por target em CLI, `audit.source_sync_run.source_name`,
`EXPECTED_SOURCES` e mensagens: `ml_cross_company` e `tiktok_channel_efficiency`.
Um teste trava essa igualdade — nome divergente faria o health check monitorar uma
fonte que ninguem grava e reportar "nenhuma execucao registrada" para sempre.

**Execucao.** Duas entradas em `EXPECTED_SOURCES`, `critical=True`, cadencia diaria,
threshold de **30h** — o mesmo contrato das outras fontes diarias criticas. Execucao
ausente, com `status=failed` ou mais antiga que 30h torna `ok_critical=false`.

**Cobertura do dado.** Duas consultas read-only novas em `fetch_data_freshness`:

| Tabela | Sinal | Por que |
| --- | --- | --- |
| `fact_ml_cross_company_summary` | `MAX(synced_at)` | a fonte e' snapshot **sem** data de negocio; usar o campo de auditoria da propria fotografia evita fabricar uma data que a fonte nao tem. Mesmo padrao ja adotado para `fact_ml_produto_ranking` (`MAX(refreshed_at)`) |
| `fact_tiktok_channel_efficiency_daily` | `MAX(date)` | tem data diaria; o teto normal e' D-1, entao um dia de defasagem e' o estado correto e cabe no limite de 3 dias |

Tabela vazia ou `MAX(...)` NULL cai no ramo que ja devolve `stale=True` com
"tabela sem nenhuma linha" — **ausencia nunca e' convertida em zero nem em fresco**.
Data no futuro tambem nunca e' fresca.

**Execucao e cobertura sao sinais distintos, e os dois foram preservados.** Um sync
pode rodar com sucesso todo dia e ainda servir dado que parou de avancar, porque a
fonte upstream parou; e a tabela pode ter dado recente de uma carga anterior
enquanto o sync parou de executar. Os dois casos tem teste proprio.

Nenhuma leitura do Data Mart foi introduzida no health check, nenhuma comparacao
Gold x Marts, nenhum `COUNT(*)` integral, nenhum retry, nenhuma escrita corretiva e
nenhum fallback silencioso. O fake do banco nos testes passou a **falhar alto** para
qualquer consulta de frescor sem ramo explicito: sem isso, uma consulta nova cairia
no retorno generico e o teste ficaria verde provando nada.

Shopee segue **nao critica** nas quatro entradas conhecidas, e as regras das fontes
antigas nao mudaram — travado por teste.

**Lacuna que permanece, declarada:** as tres fatos de serving do O1
(`fact_ml_gestao_diaria`, `fact_tiktok_brand_content_daily`,
`fact_tiktok_creator_daily`) tambem **nao** tem entrada em `EXPECTED_SOURCES` nem
verificacao de frescor, porque os syncs do O1 nao registram em
`audit.source_sync_run`. E' a mesma classe de lacuna, fora do escopo deste finding,
e fica registrada aqui como divida.

### 38.12 Ordem OBRIGATORIA da primeira publicacao (Task 3/3)

Nada disto foi executado. A sequencia importa porque duas das etapas, feitas fora de
ordem, quebram producao:

1. **incorporar, sem force, o hotfix concorrente JA PUBLICADO.** Situacao medida em
   18/08/2026: `origin/main` esta em
   `a1c5ffeb01a77978192f7dff88eaa38b776ece44` — *fix(pipelines): impede efeitos
   colaterais no help regional*, de 18/08 12:35:45 −0300. Nao e' mais uma hipotese:
   o commit existe e esta na main;
2. **reconciliar a base da implementacao S3 com a `origin/main` atual.** O worktree
   isolado do S3 esta em `d04306e` e portanto **um commit atras**. O hotfix altera
   exatamente dois arquivos — `pipelines/ops/sync_region_if_needed.py` e
   `pipelines/tests/test_ops_sync_region_if_needed.py` — e **nenhum** deles esta
   entre os 25 que o S3 toca: **zero conflito de caminho conhecido**. A incorporacao
   nao foi feita nesta rodada, de proposito;
3. **nunca publicar o backend antes de o schema e os dados estarem prontos**;
4. **aplicar as migrations 009, 010 e 011** no Neon, nessa ordem;
5. **validar schema e Alembic** (head unico, tabelas e colunas presentes);
6. **executar exatamente os backfills/snapshots iniciais autorizados** — nada alem;
7. **reconciliar fonte x Marts**, com os criterios da secao 38.9;
8. **validar `/produtos` do Mercado Livre antes e depois**: `sync_produtos_ml` deixou
   de ser upsert e passou a **substituicao integral**, e `marts.fact_ml_produto_ranking`
   alimenta tambem essa superficie **ja existente**. Contraprova de nao regressao e'
   obrigatoria — nao basta validar `/inteligencia`;
9. **somente depois da prontidao do banco**, versionar e enviar o codigo que pode
   acionar deploy do backend;
10. **atualizar o checkout operacional por fast-forward** antes da proxima execucao
    das 06:00;
11. **smoke dos endpoints** e, posteriormente, observar uma execucao agendada real
    com os doze steps.

Dois riscos explicitos de inverter a ordem:

- **um push antecipado pode fazer o Render consultar tabelas que ainda nao existem.**
  O backend publicado passaria a ler `marts.fact_ml_cross_company_summary` e
  `marts.fact_tiktok_channel_efficiency_daily`; sem as migrations aplicadas, as duas
  rotas quebrariam — e hoje elas ao menos falham por um motivo conhecido;
- **atualizar o checkout operacional antes das migrations pode fazer o proximo
  `full_daily` falhar no preflight.** `check_serving_s3_tables` e
  `check_tiktok_product_content_columns` bloqueiam os steps novos enquanto as
  tabelas/colunas nao existirem, e os steps sao `critical=True` — o pipeline inteiro
  reportaria FAILED.

**Nenhuma dessas acoes foi executada nesta correcao.**


## 39. Gate S3 Task 3/3 — publicacao inicial: migrations aplicadas, quatro cargas e a correcao de escala

**Resultado: `SUCCESS` na publicacao dos dados; backend ainda dependente de deploy manual.**
Executado em 18/08/2026, no worktree isolado do S3, com o checkout operacional
intocado durante toda a operacao.

### 39.1 Integracao do hotfix

`origin/main` estava em `a1c5ffe` (*fix(pipelines): impede efeitos colaterais no
help regional*). Os 25 arquivos da Task 2 foram commitados em `6e6f152` sobre
`d04306e` e **rebased sem force** para `868177d`, cujo pai e' `a1c5ffe`. Prova de
integridade: os 25 blobs do S3 ficaram byte a byte identicos ao pre-rebase, e os
dois arquivos do hotfix identicos aos de `a1c5ffe`. Zero conflito.

Validacao pos-rebase: `pipelines/tests` **2474 passed** (+11 do teste que veio com
o hotfix), suite da API **626 passed** com o ambiente carregado apenas em memoria,
contratos de `/inteligencia` (63), `/brand-detail` (105) e `/produtos` (62),
`compileall` em 9 modulos, head unico do Alembic em `011`, e o DDL offline de
`008:head` gerando 2 `CREATE TABLE`, 1 `CREATE INDEX`, 2 `ALTER TABLE` e 3
`UPDATE alembic_version`, **sem nenhum `DROP`/`DELETE`/`TRUNCATE`**.

### 39.2 Concorrencia observada: um segundo ator no mesmo dia

O audit log esta em **UTC**, e revelou **duas** execucoes completas em 18/08, nao
uma:

| Janela (local) | Origem | Rastro em `logs/` |
|---|---|---|
| 06:00–06:03 | `full_daily` agendado | sim |
| **11:10–11:33** | **invocacao direta** | **nenhum** |

A segunda rodou `shopee_daily`, `shopee-stats`, `shopee-ads`, `ml_daily`,
`tiktok_daily`, `ml_produto_ranking` e `marketplace_region_daily`, e deixou
`fact_marketplace_region_daily_backup_20260818_113303`. Como nao passou pelo
`run_with_lock`, **`logs/*.lock` nao a detectaria**. Por isso esta task passou a
comparar `MAX(sync_run_id)` de `audit.source_sync_run` contra um baseline antes de
cada escrita — cinco gates, todos aprovados, nenhuma run de terceiro durante a
janela. **Divida:** canalizar toda execucao manual pelo wrapper de lock, ou
combinar janelas, antes da proxima operacao concorrente.

### 39.3 Migrations 009–011 aplicadas

`alembic upgrade head`, **uma tentativa**, exit 0, `008 -> 009 -> 010 -> 011`.

| Objeto | Estado apos a migration |
|---|---|
| `marts.fact_ml_cross_company_summary` | PK `(brand)`, 9 CHECKs `>= 0 AND <> 'NaN'`, **0 linhas** |
| `marts.fact_tiktok_channel_efficiency_daily` | PK `(date, brand, channel)`, 6 CHECKs, indice `idx_ftced_brand_date`, **0 linhas** |
| `fact_tiktok_product_daily.active_videos` | `bigint`, nullable, sem default |
| `fact_tiktok_product_daily.video_views` | `bigint`, nullable, sem default |

**Zero** relacao preexistente alterada, zero tabela removida.

### 39.4 D1 reprovou por guardrail — e o guardrail estava certo

A primeira tentativa de `sync_produtos --source ml` abortou em 13,4 s com
`staging divergiu da fotografia no fingerprint`, **sem escrever nada**. Prova de
rollback integral: `fact_ml_produto_ranking` permaneceu com 1.648 linhas, 1.648
chaves, fingerprint `d9732f278813082a` e as quatro somas byte a byte iguais a'
fotografia previa. Audit `sync_run_id=170`, `status=failed`, `ext=0 load=0`, zero
advisory lock pendente.

**Causa medida.** `gold.ml_produto_ranking` usa `NUMERIC` sem escala e carrega
precisao cheia; o destino declara escala; e a staging e'
`CREATE TEMP TABLE (LIKE marts.fact_ml_produto_ranking)`, herdando as escalas do
destino. O PostgreSQL arredonda no `INSERT`, e o codigo comparava o `Decimal`
bruto da Gold com o valor **ja arredondado**:

| Coluna | Gold | Marts | Linhas afetadas |
|---|---|---|---|
| `cumulative_revenue_pct` | `numeric` | `numeric(8,4)` | **1.388 de 1.648** |
| `gross_revenue`, `ad_spend`, `estimated_margin` | `numeric` | `(18,2)` / `(14,2)` | 0 |

Exemplo real: `94.838155642022304628` -> `94.8382`. Os agregados passavam porque
as quatro colunas somaveis ja vem com duas casas da fonte — so' o fingerprint
acusava. **O arredondamento nao e' perda nova:** o upsert anterior ao Gate S3
gravava exatamente os mesmos valores. O defeito estava no contrato de
reconciliacao, nao na carga.

### 39.5 A correcao: projecao canonica para os tipos do destino

Commit `3559ab7` — *fix(pipelines): alinha fingerprint ml a escala do destino*.

- `ml_target_numeric_scales(cur)` le' `data_type`/`numeric_scale` do
  `information_schema` e **levanta antes do advisory lock e do `DELETE`** se
  codigo e schema divergirem: coluna da allowlist ausente, que deixou de ser
  `numeric`, escala nula/invalida, ou coluna de negocio que virou `numeric` com
  escala **sem** entrar na allowlist. Nenhuma escala hardcoded.
- `_ml_quantiza(valor, escala)` reproduz o cast para `NUMERIC(p,s)` com
  `ROUND_HALF_UP`, que e' *ties away from zero* como no PostgreSQL — e nao o
  meio-par default do Python. Conferido contra o **banco real em 21 casos**,
  incluindo empates negativos: **zero divergencia**.
- `ml_project_to_target(rows, escalas)` devolve lista NOVA; `rows` nunca e'
  mutada e segue sendo o que vai para a staging, para que quem arredonda continue
  sendo o banco.

Fingerprint, agregados esperados e as comparacoes de staging e destino passaram a
usar a fotografia projetada. O fingerprint **reportado** e' o tipado; o bruto fica
em `checks["fingerprint_raw"]` para rastreabilidade. Sem tolerancia, sem epsilon,
sem float — `float` em coluna `numeric` virou erro explicito. Guardrail
**nao afrouxado**: diferenca acima do arredondamento declarado, em texto, data,
chave, `NULL` ou coluna nao numerica continua reprovando com rollback integral.
36 testes novos; `pipelines/tests` foi a **2510 passed**.

### 39.6 As quatro cargas

Uma tentativa cada, precedida de gate de writer/lock/audit e de reconferencia da
fonte.

| # | Comando | Resultado | Audit |
|---|---|---|---|
| D1 | `sync_produtos --source ml` | 1.648 apagadas / **1.648 publicadas**, `EXCEPT (0,0)`, 17,5 s | `171 success` |
| D2 | `sync_produtos --source tiktok --full` | 214.573 fonte / **214.573 upserted**, 167,5 s | `172 success` |
| D3 | `sync_serving_snapshots --target ml_cross_company --apply` | 0 apagadas / **4 publicadas**, `EXCEPT (0,0)` | `173 success` |
| D4 | `sync_serving_snapshots --target tiktok_channel_efficiency --apply` | 0 apagadas / **4.743 publicadas**, `EXCEPT (0,0)` | `174 success` |

`run_id`: `s3-init-ml-cross-20260818` e `s3-init-tiktok-eff-20260818`. O registro
`170 failed` foi **preservado**.

Em D1 o fingerprint bruto da fonte (`c1390487d0c9…`) e o tipado
(`adfd4a5bbaa02847…`) **diferem**, exatamente como a correcao preve; o tipado
conferiu contra staging e destino.

### 39.7 Reconciliacao

**ML produto.** 1.648 = 1.648 = 1.648 (fonte, fotografia tipada, destino),
`EXCEPT (0,0)`, zero chave nula, zero duplicidade, zero `NaN`. O destino confere
com o fingerprint que a propria execucao reconciliou (`adfd4a5bbaa02847…`).
Contra a fonte relida ~25 min depois ha' deriva em 27 chaves — 26 com receita
maior (venda nova, +R$ 4.039,34 / +49 unidades) e **uma com queda de
R$ 69,90 e −1 unidade, com `cancel_rate_pct` subindo de 3,0600 para 3,07: um
cancelamento**. `cumulative_revenue_pct` divergiu em 808 chaves porque e' um
acumulado: a mudanca de um produto desloca o percentual de todos abaixo dele no
ranking. Deriva **integralmente explicada** por restatement pos-snapshot, o mesmo
principio ratificado no Gate S2 — consistencia e' relativa a' fotografia da
execucao, nao igualdade eterna.

**TikTok produto.** 214.573 chaves na fonte, 214.573 no destino, **zero chave
ausente e zero extra**. `active_videos` e `video_views`: **zero divergencia**,
zero nulo nas duas pontas, **zero zero fabricado**, somas identicas a' fonte
(12.347.070 e 1.964.308.065). As unicas diferencas de valor estao em
`pct_gmv_video` (25.852), `pct_gmv_live` (27.408) e `pct_gmv_card` (33.345) e sao
**exatamente** o arredondamento declarado do destino (`numeric` -> `numeric(8,4)`):
apos projetar a fonte para a escala, o residuo e' **zero** nas tres. Comportamento
preexistente da tabela, nao introduzido pelo S3.

**ML cross-company.** 4 = 4, `EXCEPT (0,0)`, fingerprint **identico a' fonte
viva** (`0fbe1a75dd165cd1…`), quatro marcas, `date_from`/`date_to` `NULL`,
`synced_at` preenchido, zero agregado divergente.

**TikTok efficiency.** 4.743 = 4.743 no grao `(date, brand, channel)`,
`EXCEPT (0,0)`, fingerprint **identico** (`b5a3ea91b2c0e30b…`), datas
2025-10-05..2026-08-17 — **zero data futura** —, cinco marcas e os tres canais
`LIVE`/`PRODUCT_CARD`/`VIDEO`, zero agregado divergente.

### 39.8 Isolamento

Das 39 tabelas de `marts`, **37 com contagem identica** a' fotografia
pos-migration; as duas que mudaram sao as autorizadas (`0 -> 4` e `0 -> 4.743`).
**Zero** relacao fora de escopo alterada: Shopee, regional, daily marketplace,
gestao diaria, creator, brand content e dimensoes intactas. No audit, apenas os
cinco registros desta task (170 a 174).

### 39.9 `/produtos` ML e as rotas novas

As **oito** chamadas do baseline (duas paginas, top-100 ordenado, summary e uma
por marca) repetidas apos as cargas: **8/8 em HTTP 200**, esquema identico,
conjunto de campos identico, paginacao e filtros identicos, contagens identicas
(25/25, 100/100, 4 buckets). Valores mudaram — e' o snapshot novo, e o destino
foi provado identico a' fotografia da execucao. Nenhum item autoritativo perdido:
o `EXCEPT` contra a fonte e' `(0,0)`.

`/inteligencia` e `/brand-detail` foram exercitadas **com o engine do Data Mart
substituido por um objeto que levanta em qualquer `connect()`**:

| Rota | Status |
|---|---|
| `/inteligencia` | **200** |
| `/brand-detail?brand=apice&ref_month=2026-07` | **200** |
| `/brand-detail?brand=apice&ref_month=2026-06` | **200** |
| `/brand-detail?brand=barbours&ref_month=2026-07` | **200** |
| `/brand-detail?brand=barbours&ref_month=2026-06` | **200** |

**Zero** query roteada para o Data Mart. Payload de `/inteligencia` completo:
`ltv` 4, `organic` 20, `pareto` 16, `scale` 20, `signals` 4, `tk_products` 25,
`urgent` 30 — nenhuma secao vazia, nenhum valor inventado para `NULL`. Antes das
migrations as duas rotas retornavam **500**; e' a diferenca que fecha o Gate G4
para elas.

### 39.10 Health check

`STATUS CRITICO: OK` (exit 0). `ml_cross_company` e `tiktok_channel_efficiency`
com ultimo sucesso ha' 0,2 h, dentro do limite de 30 h, e as duas coberturas
frescas (`MAX(synced_at)` 0d e `MAX(date)` 1d, limite 3d). `shopee_product_monthly`
segue `[ATRASADA-CONHECIDO]` e **nao critica**. Invariantes do Bug 8 sem
divergencia. Nenhuma fonte nova ausente, `failed` ou `stale`.

### 39.11 O que continua em aberto

1. **Backend nao publicado.** O Render segue exigindo acao do proprietario; as
   rotas so' respondem 200 **localmente**. Afirmar que `/inteligencia` e
   `/brand-detail` estao no ar seria falso.
2. **Airflow inexistente** e nao configurado. Nada neste gate depende dele.
3. **Observacao agendada pendente:** a execucao do `full_daily` com os **12
   steps** ainda nao foi observada. A primeira oportunidade e' 19/08 as 06:00.
4. **Divida de auditoria dos syncs O1:** `serving_ml`, `serving_tiktok_brand` e
   `serving_tiktok_creator` continuam sem registro proprio em
   `audit.source_sync_run` — o `serving_refresh` nao audita por fonte. Divida
   anterior, fora do escopo do S3.
5. **Concorrencia:** ver 39.2.
6. **Escalas estreitas em outras tabelas.** `fact_tiktok_product_daily` tem o
   mesmo padrao (`numeric` -> `numeric(8,4)`) em `pct_gmv_*`. Nao e' defeito, mas
   qualquer reconciliacao futura por fingerprint nessas colunas tera' de projetar
   para a escala do destino, como o ML passou a fazer.
7. **06:00 continua divida:** confiabilidade depende de notebook ligado, usuario
   logado e VPN ativa.
