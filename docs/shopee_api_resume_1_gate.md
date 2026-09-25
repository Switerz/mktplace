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
| **produtos (performance)** | manual | manual | manual | manual | manual | `SHADOW` — API pronta, ver §4 |
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

🔴 **Classificação: MATURAÇÃO, não definição** — e a distinção decide tudo o que
vem depois.

A tentação é parar em "são definições diferentes de status" e tratar o gap como
incomparável. **Está errado.** O cruzamento do status do export contra o status
atual da API, pedido a pedido (barbours, julho, 11.595 pedidos), mostra que
tudo o que não foi cancelado **amadureceu para `completed`**:

| status no export (snapshot) | status na API hoje | pedidos |
|---|---|---|
| Concluído | `completed` | 8.236 |
| Cancelado | `cancelled` | 2.577 |
| Entregue | `completed` | 1.718 |
| "pode pedir uma devolução até…" | `completed` | 1.076 |
| Enviado | `completed` | 538 |
| Pedido Recebido | `completed` | 24 |
| A Enviar | `completed` | 18 |

Ou seja: a Torre e a API usam **o mesmo conceito** (`Concluído` ↔ `completed`).
O que difere é o **instante da medição**. O export congelou julho antes de os
pedidos concluírem; a API mostra o estado de hoje. Os 3.354 pedidos que
concluíram depois do export valem os ~30% que faltam.

⇒ **A Torre está subcontando julho em R$ 321.769 só na barbours**, e isso não se
corrige sozinho: o export é snapshot e ninguém reexportou.

---

## 4. Produtos — decisão tomada e o que foi implementado

**Decisão do Mário (25/09):** a tela de produtos **mantém o recorte `Concluído`**.

O gold da API não carregava dimensão de status — `units`, `gmv`, `orders` e
`lines` já vinham agregados sobre `is_sale`. Servir a tela a partir dele exigiria
inflar o número publicado com o que ainda está em trânsito.

✅ **Implementado:** `units_completed` e `gmv_completed` em
`dbt/shopee/models/gold/shopee_product_daily.sql` (branch
`feat/shopee-gold-completed-cut` no `goca-se/airflow`), subconjuntos de
`units`/`gmv`. Medido no grão do modelo, 25/09/2026:

| mês | `gmv_completed / gmv` |
|---|---|
| julho | 100,0% (4 marcas) |
| agosto | 100,0% (lescent 99,98%) |
| **setembro** | **70,6% a 79,1%** |

Em mês fechado as duas colunas são a mesma coisa; a diferença só existe no mês
corrente e é exatamente o que está em trânsito. Por isso `gmv` ficou como está:
trocar um pelo outro encolheria o mês corrente em 21-29% sem erro de dado algum.

### ⚠️ O que a troca de fonte vai fazer com o número publicado

Como §3.3 mostrou, o gap não é definição — é maturação. Então **migrar a tela
para a API vai ELEVAR o faturamento histórico por SKU em +38% a +54%**:

| marca | julho publicado hoje | julho pela API (`completed`) |
|---|---|---|
| apice | 260.769,09 | 380.538,61 |
| barbours | 741.222,83 | 1.062.992,21 |
| lescent | 176.636,52 | 272.458,84 |
| rituaria | 321.342,95 | 443.895,59 |

Isso é **correção de um número velho**, não inflação: os pedidos concluíram de
verdade. Mas é mudança visível de série histórica e **precisa ser comunicada**
antes do publisher entrar — não pode aparecer como surpresa numa segunda-feira.

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

✅ **Probe escrito e pronto para disparo:** `dags/shopee_ads_capability_probe.py`
(branch `feat/shopee-ads-capability-probe` no `goca-se/airflow`). Ele trata o
path como **hipótese**, não contrato, e separa no veredito as duas perguntas —
o endpoint existe? esta conta tem permissão nele? — porque confundi-las produz a
mesma conclusão errada por dois caminhos. A matriz de saída é **por conta**, e
nunca agrega num sim/não do canal.

O disparo é manual e passa pelo Mário: a DAG nasce pausada e a autenticação do
Airflow não passa por esta sessão (§7).

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

**Nenhuma fonte foi trocada nesta sessão.** As duas branches abertas no
`goca-se/airflow` são aditivas e não mudam o que qualquer tela lê hoje:

| branch | conteúdo | efeito em produção ao mesclar |
|---|---|---|
| `feat/shopee-gold-completed-cut` | duas colunas novas no gold | nenhum — colunas aditivas |
| `feat/shopee-ads-capability-probe` | DAG de diagnóstico | nenhum — nasce pausada, `schedule=None` |

A troca de fonte da tela de produtos é um passo separado (§8.4), e depende de
comunicar a mudança de série descrita em §4.

**As branches não foram enviadas ao remoto.** O `git push` foi recusado pelo
classificador de segurança da sessão. Os commits existem localmente; o envio
precisa ser feito pelo Mário (comandos no relatório de entrega).

---

## 8. Próximos passos, na ordem

1. ✅ **Decisão tomada:** a tela de produtos mantém `Concluído` (§4).
2. ✅ **Recorte implementado** no gold (`feat/shopee-gold-completed-cut`).
3. ✅ **Probe de Ads escrito** (`feat/shopee-ads-capability-probe`).
4. ⏳ **Enviar as duas branches e abrir os PRs** para `develop`. Passo do Mário —
   o push foi recusado pelo classificador desta sessão.
5. ⏳ **Reexportar Shopee** e recarregar o Data Mart: o silver está 32 dias
   parado, e agosto em `fact_shopee_product_monthly` só sai do zero com export
   novo. **Não depende de nada nesta lista** — é o item de maior efeito
   imediato sobre o que a tela mostra hoje.
6. ⏳ **Disparar o probe de Ads** (DAG pausada, um run manual). Decide se Ads sai
   de `BLOCKED`.
7. ⏳ **Publisher Data Mart → Neon** para produtos, por marca, com `kokeshi` em
   `manual_source`. Depois de 4, e depois de comunicar a mudança de série (§4).
8. ⏳ **Kokeshi:** registrar app no console Shopee da marca (§2). Não bloqueia
   nada acima.

---

## 9. Veredito final por dataset

| dataset | veredito | o que falta |
|---|---|---|
| pedidos | `API_ACTIVE` no Data Mart · `MANUAL` na Torre | publisher (§8.7) |
| catálogo | `API_ACTIVE` no Data Mart | nada — não há consumidor na Torre |
| produtos (performance) | `SHADOW` — API pronta e reconciliada, Torre ainda manual | merge do recorte + publisher + comunicar a série |
| shop stats | `MANUAL` | nada a ativar — a Open API v2 não tem a métrica |
| Ads | `BLOCKED` | disparar o probe (§8.6) |
| kokeshi (todos) | `MANUAL` | app no console Shopee da marca |

**Mudança de veredito em produtos:** entrou neste gate como `BLOCKED` por falta
de decisão e de recorte de status. Sai como `SHADOW` — a fonte existe, está
fresca, reconcilia contra o export em −0,16% a −0,67%, e o recorte que a tela
precisa está implementado. O que falta é operação, não descoberta.
