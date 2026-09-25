# PMA-REF-LINK-1 — referências, kits, links e frescor

Medido em **2026-09-25** contra a fotografia publicada em produção, com o próprio
matcher (`app.services.pma_match`) rodando offline sobre as três fatos. A
partição publicada foi reproduzida sem uma linha de diferença antes de qualquer
conclusão ser tirada:

| canal | observação | monitorados | comparáveis | sem referência | inativos |
|---|---|---:|---:|---:|---:|
| Mercado Livre | 2026-09-21 | 872 | 144 | 567 | 161 |
| Shopee | 2026-09-23 | 695 | 152 | 440 | 102 |
| TikTok | 2026-09-22 | 1.215 | 103 | 810 | 302 |

> **A referência não tem 221 linhas.** O enunciado do gate parte de 221, que é o
> snapshot de 02/09. O serviço usa `SQL_LATEST_SNAPSHOT` — o mais recente —, e
> hoje isso é `pma-ref:20260923T193400Z`, com **247 linhas**. Toda a auditoria
> abaixo usa as 247.

---

## 1. Causas da ausência de referência — 1.817 ofertas, nenhuma sem explicação

Classificação determinística, precedência fixa, exatamente uma causa por oferta.

| causa | ML | Shopee | TikTok | total |
|---|---:|---:|---:|---:|
| kit sem composição | 0 | 264 | 336 | **600** |
| produto ausente da tabela B2B | 408 | 95 | 122 | **625** |
| marca sem tabela B2B | 132 | 72 | 75 | **279** |
| item fora do escopo de negócio | 0 | 0 | 198 | **198** |
| SKU fora do cadastro interno | 23 | 2 | 18 | **43** |
| associação recuperável pela ponte interna | 0 | 3 | 40 | **43** |
| código antigo × novo | 3 | 4 | 14 | **21** |
| kit com composição completa | 0 | 0 | 6 | **6** |
| kit com composição incompleta | 0 | 0 | 1 | **1** |
| SKU ausente | 1 | 0 | 0 | **1** |
| **total** | **567** | **440** | **810** | **1.817** |

### Divergência de formatação: **zero casos**

Varredura de sete normalizações (espaço, não-alfanumérico, zero à esquerda, só
dígitos, sufixo `-OLD/-NOVO/-V\d`, corte em `-` e em `_`) sobre as 1.817 ofertas:
**nenhuma** variante produziria um casamento que a igualdade exata não produz. As
247 linhas de referência têm `source_sku` já em maiúscula sem espaço (0 exceções),
`source_gtin` 100% EAN de consumidor e marca sem acento. **Não há normalização a
corrigir.** Um resultado negativo medido, não uma suposição.

### Alcance da tabela B2B

- 246 das 247 linhas casam com um `produto_sk` interno — a ponte é confiável.
- **172 das 247 linhas são efetivamente usadas** por algum anúncio.
- As 75 não usadas: Yenzah 44 (marca sem catálogo em nenhum canal), Barbours 17,
  Kokeshi 6, Apice 5, Rituária 3.
- Em produtos distintos, não em anúncios: ML 338 sem referência contra 89
  comparáveis; Shopee 428 × 152; TikTok 805 × 98.

### Reconciliação — os dois números que a tela publica

O payload publicava **dois totais diferentes** para "sem referência" e explicava
só um:

| canal | cartão (`kpis`) | motivos (`metrics`) | diferença | composição da diferença |
|---|---:|---:|---:|---|
| ML | 567 | 567 | 0 | — |
| Shopee | 440 | 159 | 281 | 282 kits − 1 ambíguo |
| TikTok | 810 | 251 | 559 | 404 kits + 155 fora de escopo |

Kits e marcas fora do escopo saem do elegível **de propósito** — mas nada no
payload dizia isso. Corrigido: ver seção 6.

---

## 2. Matriz dos kits — a regra é implementável e hoje alcança 4 ofertas

Regra de negócio aplicada: 2 unidades −5%, 3 −10%, 4+ −15%.

### "Número de produtos" = unidades componentes, não SKUs distintos

Registrado explicitamente porque **muda o resultado**. Na BOM
(`gold.bridge_kit_componente_gobeauty`, 1.497 kits), `qty_per_kit` ∈ {1,2,3,4}:

- 83 kits (5,5%) têm nº de SKUs ≠ nº de unidades;
- **80 kits (5,3%) mudam de faixa de desconto** conforme a leitura escolhida.

Exemplo do catálogo: 47 kits com 1 SKU × 3 unidades — por SKU não teriam faixa,
por unidade caem em −10%. A leitura adotada é **unidades**. Para os 21 kits
relevantes ao PMA hoje todo `qty_per_kit` é 1, então a escolha não altera nenhum
número atual — mas altera 80 kits assim que o alcance crescer.

### Situação das 923 ofertas rotuladas kit (Shopee + TikTok, 828 chaves)

| situação | ofertas |
|---|---:|
| SKU do canal não chega ao cadastro de produto | **902** |
| kit com composição incompleta (falta referência de componente) | 16 |
| **kit com composição completa e referência calculável** | **4** |
| kit confirmado sem BOM | 1 |

**O bloqueio é cadastral e é de código, não de composição.** A BOM só existe no
Protheus, chaveada por SKU Protheus (`KBB99005`, `KRT99011`, `KAP99018`). Os
anúncios de Shopee e TikTok carregam SKU nativo do marketplace (`MLLIMPDUPL`,
`KIT045`, `01346`), que existe só no Bling e **não tem ponte para o Protheus** —
`sku_antigo` está vazio para Rituária e não cobre esses códigos nas demais.
Medido: **1 de 355** kits Shopee e **13 de 568** kits TikTok têm `codigo_protheus`.

### Os 10 kits que chegam ao cadastro

Todos Kokeshi, no TikTok, todos com composição **confirmada na fonte de verdade**
(`silver.gobeaute_produto_cadastro`, `tipo = 'KT'`) e verificada contra o título
do anúncio um a um.

| SKU canal | SKU cadastro | componentes | unidades | faltam ref. | referência calculada |
|---|---|---:|---:|---:|---:|
| 40010 | KKS00008 | 2 | 2 | 0 | R$ 65,36 |
| 40012 | KKS00010 | 2 | 2 | 0 | R$ 75,81 |
| 40018 | KKS00017 | 2 | 2 | 0 | R$ 88,16 |
| 40019 | KKS00015 | 2 | 2 | 0 | R$ 53,01 |
| 40015 | KKS00027 | 4 | 4 | 1 | — |
| 40025 | KKS00005 | 5 | 5 | 1 | — |
| 40002 | KKS00002 | 3 | 3 | 2 | — |
| 40004 | KKS00004 | 3 | 3 | 2 | — |
| 40006 | KKS00006 | 5 | 5 | 2 | — |
| 40009 | KKS00032 | 3 | 3 | 3 | — |

**Impacto na cobertura se a regra for implementada hoje: +4 ofertas no TikTok**
(103 → 107, de 29,1% para 30,2%). Zero na Shopee e no ML.

### Armadilha encontrada — `gold.dim_produto_gobeauty.nome` está errado

Quatro desses produtos têm `nome` de material de embalagem:

| produto | `dim_produto_gobeauty.nome` | `gobeaute_produto_cadastro.descricao` |
|---|---|---|
| KKS00008 | SACO PLASTICO BOLHA 19X25 | **KIT OLHOS DE GUEIXA + OLEO DE ROSA MOSQUETA** |
| KKS00032 | SACO PLASTICO BOLHA 26X36 | **KIT LINHA OZONIO KOKESHI** |
| KKS00004 | CAIXA GG — CX. 14 DISPLAY | **KIT ANTISSINAIS KOKESHI** |
| KKS00002 | CAIXA P — FACA 65 CX. 02 | **KIT ACNE CONTROLADA KOKESHI** |

Os EANs também são placeholder (`0000000000029`, `2000000000023`). A **composição**
está correta — bate com o título do anúncio nos 10 casos —, mas o `nome` do
`dim_produto` não é confiável para esses nós. Defeito a reportar ao dono do
`dim_produto_gobeauty`; **não** bloqueia a regra dos kits, e é por isso que a
verificação foi feita contra o cadastro e não contra o `nome`.

Nove kits Kokeshi do TikTok (40125–40131) têm BOM cadastrada sob a marca
**apice**, com componentes apice. Cruzamento de marca não foi aplicado.

---

## 3. Links de anúncio

### Mercado Livre — íntegro, 872/872

| verificação | resultado |
|---|---|
| `permalink` nulo ou vazio | 0 |
| esquema HTTPS | 872/872 |
| host | `produto.mercadolivre.com.br`, único |
| `item_id` presente na URL (forma `MLB-<n>`) | 872/872 |
| permalink servindo mais de um `item_id` | 0 |
| `seller_id` × marca | 1:1, quatro contas da casa |

Nenhum link quebrado, trocado ou fora de domínio. Nada a corrigir.

### Shopee e TikTok — `LINK_NAO_DISPONIVEL_NA_FONTE`

- Nenhuma coluna de URL, permalink ou slug existe em `raw.shopee_products`,
  `raw.shopee_product_models`, `silver.stg_shopee_products`,
  `gold.tiktok_product_catalog` nem em qualquer tabela `*shopee*`/`*tiktok*` do
  Data Mart. A única coluna de URL em todo o conjunto é `image_url`.
- Nenhum conector do repositório lê ou grava URL de vitrine.
- Nas descrições e `attribute_list` de `raw.shopee_products`: **0 ocorrências**
  de `http` ou `shopee.com`.
- **Os identificadores existem** — Shopee tem `shop_id` por conta (1609671923,
  1579330222, 1593864538, 1457734799) mais `item_id` e `model_id`; TikTok tem
  `product_id` e `sku_id`. Mas montar `shopee.com.br/product/{shop_id}/{item_id}`
  seria padrão observado na vitrine, **não contrato oficial documentado** da Open
  API. Fora do que este gate autoriza.

O frontend já trata isso corretamente: `urlAnuncioSegura` aplica allowlist de
domínio **por canal**, exige HTTPS e devolve `null` fora disso — a tela mostra
texto sem link, e nunca monta URL a partir de `offer_key`.

---

## 4. Links de pedido (Expedição) — bloqueados por autenticação, antes de contrato

A `marts.expedicao_fila_atual` **tem** o identificador: `marketplace_order_id`
(Shopee 365 pedidos, 14 caracteres; ML 714, 11 caracteres; TikTok não está na
fila). O bloqueio é anterior ao deep link:

1. **A API não tem autenticação.** Nenhum dos três routers registrados em
   `app/main.py` declara dependência de auth.
2. Por isso `FILA_COLUNAS` exclui `marketplace_order_id` da allowlist servida,
   deliberadamente. O que pode sair é `order_ref`, HMAC-SHA256 truncado — e só
   com `expedicao_order_ref_secret` configurado, que é `""` por padrão.
3. Um `order_ref` opaco **não serve** para deep link: o Seller Center precisa do
   `order_sn` real.

Ou seja: expor link de pedido exige **primeiro autenticar a API**, depois decidir
expor o `order_sn`, e só então existiria a questão do contrato de deep link — que
tampouco está documentado em nenhuma fonte que ingerimos.

**Recomendação: manter texto sem link.** A limitação é de segurança, não de
produto, e resolvê-la na ordem inversa exporia identificador de pedido numa rota
pública.

---

## 5. Causa do atraso — publicação manual, e um dos canais é recusado

**As fontes estão em dia.** O atraso é inteiramente de publicação:

| fonte | último dado | serving | defasagem |
|---|---|---|---:|
| `silver.stg_ml_item_price_history` | **25/09 06:03** (879 itens) | 21/09 | 4 dias |
| `raw.shopee_products` | **25/09 09:01** | 23/09 | 2 dias |
| `gold.tiktok_product_catalog` | 23/09 | 22/09 | 1 dia |

`audit.source_sync_run`, últimas execuções:

| publisher | início | status | linhas |
|---|---|---|---:|
| `channel_offer_snapshot` | 23/09 12:51 | success | 695 |
| `channel_offer_snapshot` | 22/09 23:19 | success | 1.215 |
| `channel_offer_snapshot` | 22/09 21:22 | **failed — `recusado: channel_flag_disabled`** | 0 |
| `ml_listing_price_snapshot` | 22/09 21:20 | success | 6.072 |

Dois defeitos, ambos confirmados no código:

1. **`pma_refresh` não tem agendamento.** O pipeline existe
   (`orchestrate.PIPELINES["pma_refresh"]`, 4 steps) e está mapeado em
   `run_task.ps1`, mas a única tarefa registrada no Windows é
   `mktplace_full_daily` (diária, 06:00) — e `full_daily` **não contém nenhum
   step do PMA**. O ML rodou **uma vez** em 20 dias.
2. **Shopee e TikTok são recusados dentro do `pma_refresh`.**
   `channel_offer_publisher.py:858` passa `channel_enabled=False` fixo no código,
   e `plan_publication` recusa com `REFUSE_FLAG_OFF` sem `operator_override` — que
   os steps do orquestrador **não** passam. A falha de 22/09 21:22 é exatamente
   isso. Os dois sucessos seguintes foram execuções manuais com
   `--operator-override`.

### Menor plano seguro

1. **Destravar a flag antes de agendar.** Agendar hoje produziria uma falha
   diária previsível em dois dos três canais. A flag `channel_enabled=False`
   hardcoded era o guarda-corpo do piloto; com os três canais no ar, ela precisa
   passar a ler a configuração, mantendo `--operator-override` como escape.
2. **Registrar `pma_refresh` no Task Scheduler**, depois do `full_daily` das
   06:00 — não dentro dele. As duas razões pelas quais a Shopee saiu do
   `full_daily` (Gate C1) valem aqui: um canal lento não deve derrubar o
   pipeline dos outros, e o `pma_refresh` já tem política de exit própria
   (`PIPELINES_COM_EXIT_ESTRITO`).
3. **Alerta de atraso dentro da Torre.** Já existe matéria-prima: o step
   `health_check` é `always_run` no `pma_refresh`, e o payload traz
   `freshness_status` e `lag_days` por canal. Falta só a superfície que os
   mostra sem alguém abrir a tela.

---

## 6. O que foi corrigido neste PR

Só o que é determinístico e comprovado, sem dado novo e sem migration.

| antes | depois |
|---|---|
| 1.817 ofertas com um motivo único, `reference_missing_for_product` | 4 motivos distintos, um por oferta, nenhuma órfã |
| autoridade sobre "a marca tem tabela?" era `NO_REFERENCE_BRANDS`, lista fixa do ML — dizia "nenhuma linha casou" para Gocase e Denavita | `ReferenceIndex.brands`, medido no snapshot carregado |
| `kit_composition_missing` prometido em `pma_domain` item 4 e nunca emitido | emitido nos 600 kits |
| `_metrics_do_ml` re-derivava motivos do `comparison_status` | conta do campo da linha |
| cartão 810 × motivos 251, sem nada explicando os 559 | `metrics.no_reference_breakdown`, que soma exatamente o cartão |

**Cobertura inalterada** — ML 144/711, Shopee 152/311, TikTok 103/354. Isto é
diagnóstico, não associação.

### O que foi deliberadamente NÃO implementado

**A ponte interna (SKU → produto → EAN → referência) recuperaria 64 ofertas e
não é segura.** O método `MATCH_INTERNAL` já existe em `pma_match.resolve_match`
mas nunca é exercitado: a API não recebe `internal_index` em nenhuma chamada, e
não pode — ela lê só `marts.*` no Neon, e o cadastro vive no Data Mart.

Antes de propor a fiação, as 64 associações foram conferidas contra o nome do
produto e contra o volume declarado:

| veredito | ofertas | leitura |
|---|---:|---|
| tamanho declarado bate nos dois lados | 46 | seguras |
| um dos lados não declara tamanho | 16 | não verificáveis |
| **tamanho DIVERGE** | **2** | **provadamente erradas** |

As erradas: Apice `20322`, "Shampoo Cachos **300 ml**" apontando para "SHAMPOO
CCH NUTRITIVO **1000 ML**" a R$ 114,90 — produziria "muito abaixo da referência"
sobre um produto observado a R$ 47,53. No balde não verificável há pelo menos
mais dois: Rituária `MK4MAG180` (180 cápsulas, R$ 235,90) e `MKP4MAG` (60
cápsulas, R$ 69,90) casam **na mesma linha** de referência pelo mesmo EAN
`7908407007796`; e Barbours `10066` ("My Journey") aponta para "COND PFM FIN
**ONE WAY**".

Taxa de erro conhecida de no mínimo 6%, e os erros são justamente os que geram
veredito de preço alto e falso. **Não é associação determinística comprovada.**
Se for retomada, o caminho é: (a) uma guarda de volume, (b) corrigir o EAN
duplicado entre tamanhos de embalagem no cadastro, e (c) plumbing para levar o
EAN interno até `marts` — nessa ordem.

---

## 7. Genuinamente indisponível na fonte

1. URL de vitrine de Shopee e TikTok — `LINK_NAO_DISPONIVEL_NA_FONTE`.
2. Composição de kit para 902 das 923 ofertas rotuladas kit — a BOM é do
   Protheus e o SKU do marketplace não chega até lá.
3. Tabela B2B de Lescent — 279 ofertas nos três canais.
4. Deep link de pedido no Seller Center — sem contrato oficial nas três APIs,
   e bloqueado antes disso pela ausência de autenticação na API.
5. Contexto promocional do TikTok — só `sale_price`, sem preço de tabela e sem
   `promotion_id` (já registrado no PMA-2A-R).
6. Preço de checkout nos três canais — `advertised_only` por contrato.
