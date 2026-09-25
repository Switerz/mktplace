# SHOPEE-API-RESUME-1 — inventário, reconciliação e veredito por dataset

**Data da medição:** 2026-09-25 · **Gate:** SHOPEE-API-RESUME-1
**Natureza:** leitura apenas. Nenhuma escrita em banco, nenhum token renovado, nenhuma DAG disparada.

---

## 0. A premissa do gate estava desatualizada

O gate partiu de "integração pausada em 24/07, em shadow, cobrindo 4 de 5 marcas".
**Isso não descreve o estado atual.** A integração foi retomada e levada a produção
no repositório `goca-se/airflow` entre agosto e setembro de 2026.

Medido no Data Mart em 25/09/2026:

| tabela | linhas | janela | última carga |
|---|---|---|---|
| `raw.shopee_orders` | 269.591 (4 contas) | 2025-12-31 → 2026-09-25 | **25/09 18:01 UTC** |
| `raw.shopee_products` | 662 (4 contas) | retrato | **25/09 09:01 UTC** |
| `silver.stg_shopee_orders` / `_order_items` / `_products` / `_product_models` | derivadas | idem | idem |
| `gold.shopee_product_daily` | 40.250 | 2025-12-31 → 2026-09-25 | idem |

DAGs ativas e agendadas: `shopee_orders_etl` (`0 */6 * * *`), `shopee_catalog_etl`
(`0 9 * * *`), `shopee_orders_backfill` (manual). Os horários das últimas cargas
batem com os schedules — **não é shadow, é produção corrente**.

Cobertura por conta: `apice`, `barbours`, `lescent`, `rituaria`. `kokeshi` ausente.

---

## 1. Matriz `marca × dataset × fonte`

| dataset | apice | barbours | lescent | rituaria | kokeshi | veredito |
|---|---|---|---|---|---|---|
| **pedidos** | API | API | API | API | manual | `API_ACTIVE` (Data Mart) |
| **catálogo de produto** | API | API | API | API | manual | `API_ACTIVE` (Data Mart) |
| **produtos (performance)** | manual | manual | manual | manual | manual | `BLOCKED` — ver §4 |
| **shop stats / funil** | manual | manual | manual | manual | manual | `MANUAL` — sem endpoint |
| **Ads** | manual | manual | manual | manual | manual | `BLOCKED` — escopo não provado |

⚠️ **Distinção que importa:** pedidos e catálogo estão em API **no Data Mart**.
A **Torre** (`marts.*` no Neon) continua servida por export manual em 100% dos
datasets. A API não alimenta nenhuma tela hoje.

---

## 2. Por que Kokeshi está ausente — causa nominal

Não é credencial ausente nem scope insuficiente. É **app inexistente**.

`sql/dml_config_app_shopee.sql` documenta que o app Shopee é registrado como
*Seller In House System* **no console da empresa dona de cada loja**. Cada marca
tem `partner_id` e `partner_key` PRÓPRIOS (a Ápice usa 1240636; a Barbour's,
2039504). Não existe um app da gogroup que autorize várias lojas.

Para a Kokeshi entrar: registrar um app no console Shopee da Kokeshi → Connection
`shopee__kokeshi` no GoBrands → autorização do lojista (com SMS) → backfill com
`promote=true`. **Nada disso é código.**

---

## 3. Reconciliação API × manual

### 3.1 Pedidos — fecha EXATO nas 4 marcas

Janela 01–24/08/2026 (BRT). O teto é 24/08 porque **o export do Data Mart está
congelado nessa data** (§5).

Contagem de pedidos, API (`silver.stg_shopee_orders`) × shop-stats do Seller
Center (`orders_count`, deduplicado por `(brand, stat_date)`):

| marca | API | shop-stats | diferença |
|---|---|---|---|
| apice | 1.903 | 1.903 | **0** |
| barbours | 9.632 | 9.632 | **0** |
| lescent | 5.024 | 5.024 | **0** |
| rituaria | 3.276 | 3.276 | **0** |

Contra o export de itens (`stg_shopee_order_item_snapshots`), por `order_id`:
apice 1.903 × 1.893 (29 só-API, 19 só-export), barbours 9.632 × 9.621 (56/45),
lescent 5.024 × 5.021 (25/22), rituaria 3.276 × 3.271 (16/11). As sobras são de
**borda de janela** — o export foi tirado em 25/08 e não cobre pedidos criados no
fim de 24/08 BRT.

🔴 **Armadilha medida:** somar `silver.stg_shopee_shop_stats` sem deduplicar dá
**2,04 linhas por (marca, dia)** — o mesmo dia aparece em até 5 arquivos. Sem
dedup, GMV e contagem de pedidos dobram, e a API aparenta estar 50% abaixo.
Não é diferença de definição; é dupla contagem do snapshot.

### 3.2 GMV — a API mede +2,06% a +2,97% acima do shop-stats

Após dedup, 01–24/08, `gold.shopee_product_daily` × shop-stats
(`sales_brl − cancelled − refunded`):

| marca | GMV API | GMV shop-stats | Δ |
|---|---|---|---|
| apice | 180.069,58 | 174.879,53 | +2,97% |
| barbours | 686.876,09 | 672.983,87 | +2,06% |
| lescent | 251.870,38 | 246.064,21 | +2,36% |
| rituaria | 333.602,39 | 326.144,03 | +2,29% |

**Classificação: definição.** O `gmv` do gold é GMV de produto (bruto de voucher,
sem frete); o shop-stats é a vitrine líquida. Coerente com os +4,21% medidos no
gate SH-AUTO-2A sobre 257 dias e com a faixa de +2,5–6,1% do README do módulo.
**Não é perda de dado.**

### 3.3 Produtos — a API reproduz o export; a Torre usa outra definição

Julho/2026, SKU normalizado (`upper(trim())` — `Kit112` e `KIT112` são o mesmo
SKU e sem isso aparecem como 20+ SKUs órfãos de cada lado).

GMV, três fontes:

| marca | API (gold) | export Data Mart¹ | Torre (`fact_shopee_product_monthly`) | API/export | Torre/export |
|---|---|---|---|---|---|
| apice | 380.538,61 | 382.991,20 | 260.769,09 | **−0,64%** | −31,91% |
| barbours | 1.062.992,21 | 1.064.744,41 | 741.222,83 | **−0,16%** | −30,38% |
| lescent | 272.458,84 | 274.065,00 | 176.636,52 | **−0,59%** | −35,55% |
| rituaria | 443.895,59 | 445.368,67 | 321.342,95 | **−0,33%** | −27,85% |
| kokeshi | — | 5.378.359,68 | 4.012.936,05 | n/a | −25,39% |

¹ deduplicado: um snapshot lógico por `(brand, order_id)` (maior `file_id`),
status `não cancelado`. Unidades acompanham: API −0,26% a −0,67% do export.

**A Torre NÃO está subcontando por defeito.** O gap de ~30% tem causa nominal
medida: `fact_shopee_product_monthly` conta **apenas status `Concluído`**.
Comparada contra o export filtrado em `Concluído`, a Torre fecha:

| marca | Torre | export `Concluído` | Δ |
|---|---|---|---|
| apice | 260.769,09 | 262.326,14 | −0,59% |
| barbours | 741.222,83 | 745.985,88 | −0,64% |
| lescent | 176.636,52 | 178.491,20 | −1,04% |
| rituaria | 321.342,95 | 323.554,67 | −0,68% |
| kokeshi | 4.012.936,05 | 4.042.849,73 | −0,74% |

**Classificação: status.** O gold da API agrega `is_sale` (7 status); a Torre
agrega 1 (`Concluído`). Os dois estão internamente corretos e medem coisas
diferentes.

---

## 4. Por que produtos fica `BLOCKED` — e não é falta de dado

`gold.shopee_product_daily` **não carrega dimensão de status**. Tem `units`,
`gmv`, `orders`, `lines` já agregados sobre `is_sale`. Não há como derivar dele o
recorte `Concluído` que a tela publica hoje.

Trocar a fonte sem mais nada **inflaria a tela `/produtos/shopee` em +38% a
+54%** — exatamente a classe de mudança silenciosa de número publicado que travou
os gates SH-AUTO-2 e SH-API-2A.

**Destravar exige duas coisas, nesta ordem:**

1. **Decisão escrita** sobre a definição da tela de produtos: manter `Concluído`
   ou migrar para `is_sale`. Muda o faturamento exibido por SKU em ~40%.
2. **Mudança em `dbt/shopee/models/gold/shopee_product_daily.sql`** (repo
   `goca-se/airflow`, PR para `develop`): acrescentar o recorte de status — seja
   uma coluna `gmv_completed`/`units_completed`, seja `order_status` no grão.

Só depois disso o publisher Data Mart → Neon faz sentido.

### 4.1 Defeito de produção encontrado de passagem

`marts.fact_shopee_product_monthly` está com **agosto/2026 zerado** — 188 linhas
de SKU com `gmv = 0` e `units_sold = 0` nas 5 marcas — e **setembro não existe**.

Causa medida, e **não é bug de código**: é a combinação de (a) regra `Concluído`
com (b) ingestão por snapshot. O último export carregado é de 25/08, um dia depois
do fim da janela, e nessa data quase nenhum pedido de agosto tinha amadurecido
para `Concluído` — o export de agosto tem R$ 555,83 concluídos na barbours e
**R$ 0,00 na apice**. A tela não mente sobre o arquivo; o arquivo é que foi tirado
cedo demais e nunca foi retirado de novo.

⚠️ **Isto é estrutural, não pontual.** Com regra `Concluído` + snapshot, o mês
corrente sempre lê perto de zero e só enche semanas depois, se e quando alguém
reexportar. A API não tem esse problema — relê o estado a cada 6h e a maturação
entra sozinha.

---

## 5. Frescor: duas ingestões manuais distintas

| destino | datasets | última data | última carga | situação |
|---|---|---|---|---|
| Data Mart (`silver.stg_shopee_*`) | pedidos, ads, shop-stats | **24/08/2026** | 25/08 | 🔴 **32 dias parado** |
| Neon (`marts.fact_marketplace_daily_performance`) | GMV, funil, ads | **22/09/2026** | 23/09 | ✅ fresco, 5 marcas |
| Neon (`marts.fact_shopee_product_monthly`) | produtos | **ago/2026 zerado** | 09/09 e 05/08 | 🔴 §4.1 |

A Torre tem caminho próprio de export, mais fresco que o do Data Mart. As duas
não são a mesma ingestão e não têm o mesmo frescor.

---

## 6. Shop stats e Ads

**Shop stats (`MANUAL`).** A Shopee Open API v2 expõe os módulos `shop`,
`product`, `order`, `logistics`, `payment`, `discount`. Nenhum entrega visitantes,
cliques de produto, taxa de conversão ou novos × recorrentes — as métricas que a
tela consome. Isso é Business Insights do Seller Center, não Open Platform.
**Não há o que ativar.** O GMV oficial do canal continua vindo do shop-stats
manual.

**Ads (`BLOCKED`).** Não existe uma linha de código de Ads no módulo (`grep` em
`src/shopee/` retorna zero). Além disso:

- a documentação oficial (`open.shopee.com`) **não é alcançável** desta rede, então
  a existência e a semântica de um endpoint de performance diária de CPC **não
  foram provadas** — não declaro disponível o que não medi;
- mesmo que exista, o escopo de Ads é **por app, e cada marca tem o seu** (§2).
  Seriam 4 autorizações independentes, não uma.

Para destravar: probe de escopo por conta. **Não executável nesta sessão** (§7).

---

## 7. O que esta sessão não fez, e por quê

**Não foi feito probe contra a API oficial.** O `.env` local tem
`SHOPEE_PARTNER_KEY`, `SHOPEE_ACCESS_TOKEN` e `SHOPEE_REFRESH_TOKEN`, resquícios
da fase pausada em julho e rotulados no próprio arquivo como "fase 2 — futuro".

O `refresh_token` da Shopee é de **uso único por `shop_id`**, e o token vivo das
quatro lojas está no cofre `SHOPEE_TOKENS` do Airflow. Usar a credencial local
teria dois desfechos e nenhum bom: se estiver obsoleta, falha sem informar nada;
se ainda estiver encadeada, **invalida o token de produção e derruba a ingestão
das quatro lojas**, que rodou há menos de duas horas.

O caminho seguro é o mecanismo que já existe — `shopee_runtime_contract_probe` /
`shopee_payment_capability_probe` no Airflow — e disparar DAG exige autenticação
que passa pelo Mário.

**Não foi aberto PR de código.** Os dois datasets não ativos estão bloqueados por
fatos que não se resolvem com implementação: Ads por escopo não provado, produtos
por decisão de definição. Implementar qualquer um agora seria escolher no lugar
do dono do número.

---

## 8. Próximos passos, na ordem

1. **Decisão do Mário:** definição da tela de produtos — `Concluído` ou `is_sale`?
   Bloqueia o item 3.
2. **Reexportar Shopee** e recarregar o Data Mart: o silver está 32 dias parado, e
   agosto em `fact_shopee_product_monthly` só sai do zero com export novo.
   Independe de tudo o mais nesta lista.
3. **PR no `goca-se/airflow` → `develop`:** recorte de status em
   `gold.shopee_product_daily`.
4. **Publisher Data Mart → Neon** para produtos, por marca, com `kokeshi` marcada
   `manual_source`. Depois de 1 e 3.
5. **Probe de escopo de Ads**, uma chamada por conta, pelo Airflow. Decide se Ads
   sai de `BLOCKED`.
6. **Kokeshi:** registrar app no console Shopee da marca (§2). Não bloqueia nada
   acima.

---

## 9. Veredito final por dataset

| dataset | veredito | o que falta |
|---|---|---|
| pedidos | `API_ACTIVE` no Data Mart · `MANUAL` na Torre | publisher (depende de §8.1) |
| catálogo | `API_ACTIVE` no Data Mart | nada — não há consumidor na Torre |
| produtos (performance) | `BLOCKED` | decisão de definição + recorte de status |
| shop stats | `MANUAL` | nada a ativar — API não tem a métrica |
| Ads | `BLOCKED` | probe de escopo por conta |
| kokeshi (todos) | `MANUAL` | app no console Shopee da marca |
