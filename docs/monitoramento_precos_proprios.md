# Monitoramento de preços próprios — estado real

**Última atualização:** 2026-09-02 (Gate PMA-4F — encerrado)

> Este documento separa deliberadamente **código versionado**, **migration
> aplicada**, **dado publicado**, **backend publicado**, **frontend publicado** e
> **smoke**. Cada linha diz o que de fato aconteceu — não o que está pronto para
> acontecer.

## Estado por camada

| Camada | Estado | Evidência |
|---|---|---|
| Código versionado | ✅ `d772fe8` em `main` | migration 014, sync, importador, endpoint, tela, 59 testes de tela |
| Migration no Neon | ✅ **aplicada**, `alembic_version = 014` | 012 → 014 numa execução |
| Dado ML publicado | ✅ **25.559 linhas** | janela 2026-08-03..2026-09-01 |
| Referência B2B publicada | ✅ **221 linhas**, 1 snapshot | 5 marcas |
| **Backend publicado no Render** | ✅ **publicado** — deploy manual do proprietário | rota presente no `openapi.json`; GET 200 |
| **Frontend publicado na Vercel** | ✅ **publicado** — deploy automático do push | `mktplace-gobeaute.vercel.app/monitoramento-preco` |
| Contrato em produção | ✅ **idêntico ao versionado** | 6/19/8/34 campos nas quatro classes |
| Reconciliação API × UI × Neon | ✅ fecha nos três lados | 855 = 855 = 855 |
| QA em navegador real | ✅ **executado** nos 3 viewports | 127 de 128 verificações |
| Smoke de produção | ⚠️ **PASS WITH ISSUE** | única ressalva: `favicon.ico` 404, pré-existente da Torre |
| Automação (Scheduler) | ❌ **NÃO EXISTE** | sync 100% manual |

### Gate PMA encerrado como PASS WITH ISSUE

A tela está **utilizável em produção**. O proprietário publicou o backend
manualmente no Render — o mesmo padrão de deploy manual já registrado nos
gates V3, UE3 e S2/S3 — e a Vercel publicou o frontend pelo fluxo automático
do push.

A única ressalva é `GET /favicon.ico` respondendo **404**, o que produz um erro
de console no primeiro carregamento. **Não é defeito desta frente**: não existe
favicon versionado no projeto e a resposta é 404 em qualquer rota da Torre.
Corrigir isso alteraria todas as rotas e está fora deste escopo.

## O que é este monitoramento

Compara o **preço anunciado** das lojas **próprias** no Mercado Livre com o
**preço sugerido de revenda (PDV)** das tabelas B2B.

**Não é PMA.** `reference_type = suggested_retail_pdv` e
`policy_status = not_applicable_to_own_store_monitoring`. O PDV foi medido no
Gate PMA-0 como markup aritmético sobre o preço de atacado, com razão que varia
por marca (1,50 em Barbours; 1,60 em Yenzah/Rituária/Kokeshi; 1,34–1,57 em
Ápice). Não é preço mínimo anunciado, não foi aprovado como política e não
sustenta sanção.

**Não é fiscalização de revendedor.** Os 4 `seller_id` da fonte são as 4 contas
da casa. Não existe nenhuma observação de terceiro nestas tabelas.

## Fontes e grão

| | |
|---|---|
| Fonte de preço | `silver.stg_ml_item_price_history` (Data Mart, replica física, read-only) |
| Fonte de atributos | `silver.stg_ml_items` (`attributes` → SELLER_SKU, GTIN) |
| Destino da observação | `marts.fact_marketplace_listing_price_daily` |
| Destino da referência | `marts.fact_suggested_price_reference_snapshot` |
| Grão da observação | `ref_date × marketplace × seller_id × item_id` |
| Grão da referência | append-only por `snapshot_id × reference_row_id` |

`silver.stg_ml_item_variations` está **vazia** — a ponte documentada de
`seller_sku` não existe, e por isso a chave sai de `stg_ml_items.attributes`.

## Contrato de match

Escopado por marca, ordem obrigatória:

1. **GTIN exato** dentro da marca → `brand_gtin_exact` / `primary_gtin_exact`
2. **SKU exato**, só se único na marca → `brand_sku_exact_unique` /
   `secondary_sku_unique_in_brand`

Zero fuzzy. Zero match global por SKU — a chave sempre carrega a marca, porque
Barbours, Kokeshi e Lescent têm SKU numérico de 5 dígitos no mesmo formato dos
SKUs do Ápice. Ambiguidade **não** cai para a chave seguinte: é marcada como
`non_comparable_reference_ambiguous` e nunca produz diferença.

**GTIN de consumidor:** somente 8, 12 ou 13 dígitos. DUN-14 e códigos
concatenados viram `NULL`, e a linha permanece elegível ao match secundário.

## D−1 e frescor

O sync publica no máximo **D−1** em `America/Sao_Paulo`. Somente `ref_date` igual
a D−1 sustenta comparação; anterior vira `stale_observation`; D0 ou futuro é
**fail-closed** (o endpoint levanta, porque o sync proíbe publicá-los).

O endpoint opera **exclusivamente em modo `latest`**. `ref_date` enviado na query
recebe **422 com mensagem fixa** — não é ignorado em silêncio. Comparação
histórica não existe: a referência não tem vigência, e casar um preço antigo com
a referência de hoje produziria conclusão que a fonte não sustenta.

## Cobertura — parcial, de direção indeterminada

`coverage_status = advertised_only`. Não há frete, cupom de vitrine, subsídio de
plataforma nem preço de checkout: a fonte do ML não os fornece, e as colunas
**não existem** para que ausência não seja lida como zero.

Como `checkout = produto + frete − cupom`, e o frete **eleva** enquanto o cupom
**reduz**, a direção líquida do desvio é **INDETERMINADA**. A comparação é
parcial e a diferença pode mudar de valor **e de sinal** quando esses componentes
forem considerados. Não afirme que o resultado é conservador.

## Números reais medidos (D−1 = 2026-09-01)

### Carga ML

| | |
|---:|---|
| Janela | 2026-08-03 → 2026-09-01 (30 dias) |
| Linhas | **25.559** = 25.559 chaves distintas |
| Itens | 871 · Marcas | 4 |
| Checksum de preço | 2.958.800,78 (fonte = destino) |
| `original_price` não-nulo | 8.989 (nulo = sem promoção) |
| `gtin` nulo | **519** (90 ausentes na origem + 429 inválidos normalizados) |
| Linhas em D−1 | 855 |
| `EXCEPT` bidirecional | (0, 0) |

Por marca: barbours 9.193 · kokeshi 6.575 · rituaria 5.484 · lescent 4.307.

### Referência B2B

221 linhas, 1 snapshot, 5 hashes de arquivo distintos, `EXCEPT` (0,0).

| Marca | Linhas | Com EAN | Ambíguas |
|---|---:|---:|---:|
| Ápice | 84 | 84 | 0 |
| Barbours | 47 | 44 | 0 |
| Kokeshi | 23 | 23 | 0 |
| Rituária | 23 | 23 | **3** |
| Yenzah | 44 | 44 | 0 |

### Endpoint

| KPI | Valor |
|---|---:|
| `monitored_count` | **855** |
| `comparable_count` | 138 |
| `below_reference_count` | **18** |
| `at_or_above_reference_count` | 120 |
| `no_reference_count` | 523 |
| `ambiguous_reference_count` | 0 |
| `stale_count` | 0 |
| `inactive_count` | 194 |

A soma dos seis status fecha exatamente em 855.

Match: 143 por GTIN, 12 por SKU único na marca, 700 sem match.

| Marca | Total | Abaixo | Na/acima | Sem ref. | Inativo |
|---|---:|---:|---:|---:|---:|
| barbours | 309 | 5 | 65 | 183 | 56 |
| kokeshi | 219 | 1 | 28 | 143 | 47 |
| lescent | 145 | 0 | 0 | 107 | 38 |
| rituaria | 182 | **12** | 27 | 90 | 53 |

O desvio concentra-se na Rituária. `below_reference` é **potencial desvio de
preço** que exige **revisão humana** — nunca infração.

## Tela `/monitoramento-preco`

Rota client-side em `apps/web/app/monitoramento-preco/page.tsx`, no grupo
**Inteligência** da navegação. Ela **apresenta** o que a API entrega e nada mais:
não deriva match, não recalcula status comercial, não trata `NULL` como R$ 0, não
esconde ambiguidade e não sugere sanção, punição ou obrigação legal.

**Camadas de código**

| Arquivo | Papel |
|---|---|
| `src/lib/monitoramento-preco-contract.ts` | tipos fiéis ao schema Python, montagem do query string, `MonitoramentoPrecoError`. Sem dependência de runtime — é isso que permite testá-lo com `node --test`. |
| `src/lib/monitoramento-preco.ts` | formatação pt-BR, rótulos, paginação, allowlist de domínio do link. Puro. |
| `src/lib/api-client.ts` | `fetchMonitoramentoPreco` — 24ª assinatura pública de `fetchX`. |
| `app/monitoramento-preco/page.tsx` | composição, filtros, tabela, paginação, drill-down, estados. |

**Decisões que a tela carrega**

1. **`ref_date` nunca é enviado.** O backend recusa o parâmetro com 422. O
   construtor de query o omite por construção, e há teste fixando isso.
2. **Ausência de dado imprime `—`, nunca `R$ 0,00` nem `0%`.** `shipping_amount`,
   `seller_coupon_amount`, `platform_subsidy_amount` e `checkout_price` são
   `NULL` em 100% das 855 linhas hoje (`coverage_status = advertised_only`), e a
   tela diz isso em texto, não por omissão.
3. **Zero notação compacta.** Nenhum `K`, `M`, `mil` ou `mi` — são contagens de
   anúncios e valores em reais, onde arredondar apaga a informação.
4. **A diferença preserva o sinal.** `fmtDiferenca` não usa `Math.abs`: um
   anúncio R$ 90,10 abaixo do PDV mostra `−R$ 90,10`.
5. **O filtro de situação altera só a tabela.** Marca e busca alteram os KPIs
   também. Isso preserva o denominador — filtrar por "abaixo da referência" não
   faz o total virar 18. A tela declara essa regra abaixo dos KPIs.
6. **O link do anúncio passa por allowlist** de esquema HTTPS e domínio do
   Mercado Livre; qualquer outra coisa não vira link.
7. **Guarda de frescor** por `requestKey` + `AbortController`: resposta de
   requisição vencida é descartada em vez de sobrescrever a tela.

## QA e smoke de produção — executados

### Fase 1 — backend no Render (somente GET e SELECT)

- `/api/v1/performance/monitoramento-preco` **presente** no `openapi.json` de
  produção; GET **200** nas duas páginas.
- **Contrato idêntico ao versionado**, comparado campo a campo contra
  `apps/api/app/schemas/monitoramento_preco.py`: 6 chaves de topo, 19 de
  `meta`, 8 de `kpis` e 34 por linha — zero campo a mais ou a menos.
- `observed_ref_date = 2026-09-01`, igual a D−1 em America/Sao_Paulo e igual a
  `max(ref_date)` no Neon. **Zero linhas** com `observed_at` em D0 ou futuro;
  todas as 855 na mesma data.
- Nenhum `seller_id` nem qualquer campo de PII no payload.
- `shipping_amount`, `seller_coupon_amount`, `platform_subsidy_amount` e
  `checkout_price` **NULL em 855/855**; `coverage_status` só `advertised_only`.
- `ref_date` oculto continua **422 com mensagem fixa e sem eco**, inclusive
  para `<script>x</script>` e para valor vazio.

### Reconciliação API × Neon (read-only), D−1 = 2026-09-01

| Medida | API | Neon | |
|---|---|---|---|
| monitorados | 855 | 855 | igual |
| inativos | 194 | 194 | igual |
| ativos = comparáveis + sem ref. + ambíguos | 138+523+0 = 661 | 661 | igual |
| barbours / kokeshi / lescent / rituária | 309/219/145/182 | 309/219/145/182 | igual |
| referência B2B | — | 221 linhas, 1 snapshot | — |

Soma dos seis status = 855 = `monitored_count`. A baseline anterior foi
**reproduzida integralmente** — 855/138/18/120/523/0/0/194 — porque o dado não
mudou entre as rodadas. Uma linha `below_reference` foi conferida campo a campo
contra o banco, incluindo `difference_amount` = anunciado − sugerido (−90,10).

### Fase 2 — QA em navegador real (Chromium)

**128 verificações por rodada, nos três viewports (1440×900, 1024×768,
390×844): 127 aprovadas, 1 reprovada** — o `favicon.ico` descrito acima.

Aprovado nos três viewports, contra o payload real de produção:

- tela fora do estado de erro; KPIs **iguais à API** nos seis cartões e nos
  dois indicadores de qualidade (ambíguos 0, stale 0);
- título, breadcrumb e link na navegação lateral;
- aviso observacional com as **6 ressalvas**; **zero** termo de sanção
  ("severidade", "infração", "violação", "punir", "sanção", "denúncia",
  "ilegal");
- tabela com 500 linhas e 12 colunas; moeda pt-BR; **sinal negativo
  preservado** (`−R$`); percentual formatado;
- **zero notação compacta** (K/M/mil/mi) na tabela e nos KPIs;
- ausência impressa como travessão em 1.087 ocorrências; nas 272 linhas sem
  referência, sugerido/diferença/percentual são todos `—`, **nunca zero**;
- as 10 linhas que mostram `R$ 0,00` na diferença são **zero medido**, com
  anunciado igual ao sugerido e situação "Na ou acima" — não valor fabricado;
- truncamento honesto: "Exibindo 500 de 855 anúncios";
- **paginação cobre as 855 sem duplicidade**: 500 + 355 = 855 `item_id`
  distintos, rótulos "1–500 de 855 · página 1 de 2" → "501–855 de 855 · página
  2 de 2", botões no estado certo na última página;
- filtro de marca altera tabela **e** KPI (182/182); filtro de situação altera
  **só** a tabela (18) e mantém o KPI em 855, **preservando o denominador**;
- busca por item retorna 1 linha; busca sem resultado cai no **estado vazio
  real, sem fixture**; "Limpar filtros" restaura marca, situação e busca;
- a tabela **não tem link solto** — o link do anúncio fica no detalhe, com
  `https`, domínio do Mercado Livre, `rel="noopener noreferrer"` e
  `target="_blank"`;
- drill-down: foco inicial em "Fechar detalhes", `aria-modal="true"`, **focus
  trap** retendo Tab e Shift+Tab, **Escape** fecha e o **foco volta** ao botão
  "Analisar";
- **513 alvos** medidos, nenhum abaixo de 44×44; **6.565 nós de texto**, nenhum
  abaixo de 12px;
- **zero overflow horizontal da página**, com a tabela rolando internamente
  (1587px de conteúdo em 1150/734/356px de container);
- **zero hydration warning**, zero exceção de página, zero requisição 4xx/5xx
  inesperada.

**Nenhum estado usou fixture ou interceptação.** Tudo foi exercitado com o
payload real. Os estados `stale_observation` e
`non_comparable_reference_ambiguous` têm 0 linhas no dado de hoje e por isso
**não foram exercitados em tela** — só há cobertura de teste unitário. Isso
permanece aberto.

### Defeito encontrado e corrigido — rolagem lateral da página

O smoke reprovou em algo que só um navegador real mede: **a página inteira
rolava na horizontal** nos três viewports.

| Viewport | `documentElement.scrollWidth` | excesso | deslocamento real |
|---|---|---|---|
| 1440×900 | 1766 | 326px | 326px |
| 1024×768 | 1766 | 742px | 742px |
| 390×844 | 1518 | 1128px | 1128px |

`window.scrollTo(9999, 0)` deslocava a viewport de fato, expondo fundo vazio à
direita. As outras rotas da Torre medem **excesso 0px** no mesmo viewport, o
que descartou dívida do shell: as tabelas delas são mais estreitas e não
expunham o problema.

**Causa:** as 12 colunas em `whitespace-nowrap` dão à tabela um min-content de
~1587px, e esse overflow escapava do `overflow-x-auto` do `TableScrollHint`,
propagando por toda a cadeia até o `body`.

**Correção (`d772fe8`):** `overflow-hidden` no wrapper branco da tabela — o
mesmo padrão que `/pedidos` já usa. Uma classe. A rolagem interna da tabela
continua intacta e o `TableScrollHint` segue sendo quem rola. Uma primeira
hipótese — `min-w-0` na `section`, por flex item com `min-width: auto` — foi
testada, **não mudou nada** e foi revertida em vez de mantida inerte. Há teste
de regressão fixando o recorte no wrapper.

Depois do redeploy da Vercel, o smoke afetado foi reexecutado: **excesso 0px
nos três viewports**.

## Escopo de marcas

- **Comparáveis:** barbours, kokeshi, rituaria
- **`no_reference`:** lescent — monitorada no ML, sem tabela B2B
- **`out_of_scope_no_ml_catalog`:** apice, yenzah — têm referência B2B, não têm
  catálogo próprio no ML. Recusadas com 422 no filtro de marca

## Zero PII

O bloco cadastral das planilhas (linhas 1–31: razão social, CNPJ, I.E., CEP,
endereço, bairro, cidade, estado, telefone, e-mail) **não é lido nem
persistido**, e não há coluna para ele. Varredura por valor sobre as 221 linhas
publicadas: **zero** CNPJ, CPF, e-mail, telefone ou CEP formatados.

> Nota de auditoria: um scan com regex permissivo acusa 6 falsos positivos nas
> 3 linhas Barbours cujo `quality_notes` registra o DUN-14 recusado. São corridas
> de 14 dígitos **sem separador** — código de barras de caixa, não documento. O
> padrão estrito (`NN.NNN.NNN/NNNN-NN`) encontra zero, e `product_name`/
> `source_sku` não têm nenhuma corrida de 11+ dígitos.

## Operação — hoje 100% manual

```
# 1. preço anunciado (janela de 30 dias terminando em D-1)
python -m pipelines.sync_ml_listing_price_serving --lookback-days 30            # diagnóstico
python -m pipelines.sync_ml_listing_price_serving --lookback-days 30 --apply --run-id <id>

# 2. referência B2B (append-only; cada execução cria um snapshot novo)
python -m pipelines.pma.reference_import --file <5 xlsx> --out <fora do repo>   # diagnóstico
python -m pipelines.pma.reference_import --file <5 xlsx> --out <...> --apply --run-id <id>
```

Sem `--apply` nada é escrito. **Não há agendamento**: nenhuma entrada no
Scheduler, nenhum step no `full_daily`.

### Armadilhas operacionais conhecidas

1. **`apps/api/alembic/env.py` sobrescreve `sqlalchemy.url` com
   `settings.database_url`.** Passar URL por outra via não muda o destino — o
   único alvo efetivo é a variável `DATABASE_URL`. Confirme o destino **antes**
   de qualquer `alembic upgrade`.
2. **`alembic.ini` tem `script_location` relativo ao cwd**, que colide com o
   pacote `alembic` instalado quando se roda de `apps/api`
   (`ModuleNotFoundError: alembic.config`). Contorno: invocar com
   `script_location` absoluto.
3. **3 execuções não terminais antigas** em `audit.source_sync_run`
   (`tiktok_daily` 16/07 e 24/08, `shopee_daily` 16/07) com `finished_at` nulo.
   São resíduos de outras frentes, não jobs vivos.

## Limitações abertas

1. **A referência B2B não tem vigência temporal declarada**
   (`validity_status = missing`). As planilhas não têm `valid_from`/`valid_to`; o
   `captured_at` do snapshot **não é vigência** e não é usado como tal. Por isso
   não existe comparação histórica.
2. **Rituária tem 3 linhas de referência ambíguas** — `RT01016` duplicado com
   dois EAN diferentes, e o EAN `7901128300047` em dois SKUs com PDV divergente
   (R$ 109,90 e R$ 109,01). Marcadas, **não resolvidas por adivinhação**. Exige
   decisão de Trade/pricing na tabela de origem. Nenhum anúncio do ML casou com
   elas, então `ambiguous_reference_count = 0` hoje.
3. **Lescent sem referência** — monitorada, sem tabela B2B.
4. **Ápice e Yenzah fora de escopo** — sem catálogo próprio no ML.
5. **Shopee e TikTok fora deste monitoramento**, por ausência atual de catálogo
   confiável de preço anunciado: Shopee só tem preço transacional de export de
   pedido e a API oficial está pausada; `gold.tiktok_product_catalog` não tem
   coluna de preço. Amazon não tem nenhuma fonte na Torre.
6. **Atributos de estado corrente** — `seller_sku`, `gtin`, `listing_title`,
   `permalink`, `seller_id` e `catalog_listing` vêm de `stg_ml_items`, que tem
   uma linha por item e representa **hoje**, não `ref_date`. Reprocessar janela
   antiga reescreve esses atributos. `listing_metadata_updated_at` e `synced_at`
   tornam a deriva auditável.
7. **Preço de nível item, não de variação** — `variation_id` é 0 em 100% das
   linhas da fonte.
8. **Tela observacional** — sem automação de preço, sem alerta externo, sem
   workflow jurídico, sem enforcement de revendedor, sem notificação a
   revendedor.

## Próximo passo

O Gate PMA está encerrado. O que segue aberto **não** é bloqueio desta tela:

1. **Resolver as 3 linhas ambíguas da Rituária** na tabela de origem — decisão
   de Trade/pricing, não de engenharia.
2. **Exercitar em tela os estados `stale_observation` e
   `non_comparable_reference_ambiguous`** quando o dado real os produzir.
3. **`favicon.ico` da Torre** — 404 em todas as rotas, dívida pré-existente.
4. Avaliar agendamento do sync, com observação de duas execuções manuais
   consecutivas com `EXCEPT` (0,0) antes de qualquer automação.


---

# Gate PMA-H1 + D08-R — data observada em produção (2026-09-08)

Commit funcional: `e7fa7f99da15e6c499374481558d3b66bd3a1817` —
`feat(pricing): permite consulta por data observada`. Publicado no Render pelo
proprietário e na Vercel automaticamente; contrato confirmado no OpenAPI de
produção antes do smoke.

## O que mudou no contrato

Existem agora **dois modos**, e o nome do parâmetro deixou de ser ambíguo.

| | Sem `observed_date` | Com `observed_date=YYYY-MM-DD` |
|---|---|---|
| `meta.mode` | `latest` | `selected_date` |
| dia usado | maior observação ≤ D−1 | **exatamente** o dia pedido |
| dia atrasado | mostra os dados e declara a defasagem | — |
| dia sem observação | — | HTTP 200 com estado vazio tipado |
| D0 / futuro | — | 422 com mensagem fixa, sem eco |

`ref_date` continua recusado com 422, agora por **ambiguidade**: o nome não
separa a data OBSERVADA do preço da data de CAPTURA da referência, e as duas
viajam no payload. A mensagem aponta `observed_date` como substituto.

Numa data histórica a referência é o snapshot PDV **mais recente disponível
hoje** — `reference_basis = latest_available_snapshot` —, e a resposta **não
afirma** que essa referência valia naquele dia: `validity_status` segue
`missing`. A frase obrigatória viaja em `meta.comparison_basis_text` e nas
`limitations` de cada linha.

## Frescor deixou de ser categoria comercial

Este era o defeito central. `stale_observation` era um `comparison_status` e
**substituía** a classificação: com o sync atrasado, toda linha virava
`stale_observation`, os cinco cartões comerciais iam a zero e a tela aparentava
"nenhum desvio de preço" quando o que havia era atraso de pipeline.

Medido no serving real em 2026-09-08, antes da correção do dado: 855 anúncios,
sync parado em 02/09 com D−1 em 07/09, e a tela dizia `comparable_count = 0`.
Depois: `comparable_count = 134`, `below_reference_count = 16`. Os números
sempre existiram; o modelo os escondia.

Agora são duas dimensões ortogonais:

- **`comparison_status`** — partição comercial de **cinco** valores
  (`below_reference`, `at_or_above_reference`, `no_reference`,
  `non_comparable_reference_ambiguous`, `inactive_listing`) que fecha
  exatamente em `monitored_count`;
- **`freshness_status`** — `fresh` / `stale` / `historical` / `unavailable`,
  sobreposto, na linha e no `meta`, com `lag_days` medindo a defasagem em dias
  (nulo, nunca zero, quando não há observação).

`stale` e `historical` são estados distintos de propósito: a **mesma** data é
`stale` quando o modo `latest` caiu nela por falta de dado mais novo e
`historical` quando foi escolhida. Chamar as duas de atraso confundiria falha
de pipeline com uso legítimo da tela.

**Compatibilidade:** `?status=stale_observation` não quebra. Passou a ser
filtro de **frescor** (`freshness_status == stale`) e a resposta declara a
depreciação em `warnings`, com o seletor de data como caminho novo.

## Cutoff real por fonte após o D08-R

Teto imutável do gate: **2026-09-07**. Nenhuma fonte publicou 08/09.

| Fonte | Máximo publicado | Limitado por |
|---|---|---|
| Shopee Daily (orders + shop-stats) | **2026-09-07** | cutoff do gate |
| Shopee Ads | — | **BLOQUEADA**, ver abaixo |
| Shopee Raw | 20 arquivos do lote 01–08/09 | arquivo íntegro arquivado |
| Shopee Silver / Gold | 2026-08-24 | sem executor oficial versionado |
| ML Daily | **2026-09-07** | cutoff do gate (fonte alcança 08/09) |
| ML serving (`fact_ml_gestao_diaria`) | **2026-09-07** | `min(D−1, source_max)` |
| ML produtos / ranking | 2026-09-03 | **BLOQUEADO**, ver abaixo |
| TikTok Daily | **2026-09-07** | cutoff do gate (fonte alcança 08/09) |
| TikTok produtos | 2026-09-06 | máximo real da fonte |
| TikTok brand | 2026-09-06 | máximo real da fonte |
| TikTok creator | 2026-09-06 | máximo real da fonte |
| TikTok channel efficiency | 2026-09-07 | `min(D−1, source_max)` |
| Regional | 2026-09-01 | **BLOQUEADO**, ver abaixo |
| **PMA serving** | **2026-09-07** | cutoff do gate |

`serving_refresh` resolve `min(D−1, source_max)` **por target, isolado**. É o
que faz `creator` parar em 06/09 sem receber um 07/09 fabricado, enquanto `ml`
é limitado por D−1 contra uma fonte que já tem 08/09.

## Bloqueios — ausência declarada, nunca zero

**Shopee Ads — BLOQUEADA.** O parser de Ads não tem grão diário: agrega o
total do período e divide por `num_days`, onde as datas vêm do **cabeçalho do
próprio CSV**, não da janela pedida no CLI. O arquivo entregue declara
`Período,01/09/2026 - 08/09/2026` = 8 dias, com 08/09 parcial. A taxa
publicada seria 11.710,93/dia; a real está em [11.710,93 ; 13.383,92] — **até
14,3% de subestimação**, e não determinável a partir do arquivo, que só traz o
total. Nenhum parâmetro de CLI corrige o denominador. `ad_spend` ficou **NULO**
nas 35 chaves de 01–07/09 — ausência preservada como ausência.

Para desbloquear: exportar Ads com `Período` terminando em D−1.

**ML produtos / ranking — BLOQUEADO.** `gold.ml_produto_ranking` tem
`max(last_sale) = 2026-09-08`, e o ramo ML de `sync_produtos` **não tem
predicado de data nenhum** — lê a tabela inteira com `WHERE brand IN (...)`.
Não existe janela temporal segura, e rodar traria D0 em bloco.

**Regional — BLOQUEADO.** `gold.ml_gestao_diaria` alcança 08/09 e
`--incremental` não aceita teto: `run_incremental_cli(secret_path, repo_root)`
não tem parâmetro de data. `--date-from`/`--date-to` pertencem aos modos de
janela Shopee, não ao incremental. Mantido em 01/09.

**Silver / Gold Shopee — sem executor oficial versionado.**
`pipelines/staging/shopee/build_sql.py` só monta e escreve texto. A Raw pôde
ser carregada com o arquivo íntegro porque **não há propagação automática
Raw → Silver**: `shopee_batch_window.py` declara na própria linha 43 que nunca
lê `raw.shopee_ingestion_file` — ele lê `silver.stg_shopee_order_item_snapshots`.
O caminho está cortado na origem, não apenas com teto.

Risco registrado para quem construir o runner Silver: `_validate_shopee_window`
rejeita apenas `date_to > today`, ou seja **aceitaria 08/09**. Seria ali o
ponto de vazamento de D0.

## Lacuna de cobertura em 2026-09-07

O último dia observado tem **546 anúncios em 3 marcas**, contra 859 em 4 marcas
nos dias anteriores: **`barbours` não foi capturada em 07/09**. A fonte
`silver.stg_ml_item_price_history` tem o mesmo padrão (546 linhas em 07/09
contra 859 em 06/09 e 860 em 08/09), então a lacuna é da captura, não da carga.

A captura de preço é **pontual, não retroativa**: reprocessar depois não
recupera 07/09. Quem abrir a tela em `latest` verá um denominador menor e
`barbours` ausente — isso é dado honesto, não erro de publicação.

## Smoke de produção — 67/67

Executado contra o Render publicado, somente GET: `latest`;
`observed_date=2026-08-30`; data válida sem observação; D0; D+1; futuro
distante; `ref_date` depreciado; paginação em duas páginas sem sobreposição;
filtro por marca; filtro comercial; alias `stale_observation`.

Confirmado: o OpenAPI de produção traz `observed_date` público e `ref_date`
fora do schema; o enum de `comparison_status` tem exatamente os **cinco**
valores comerciais; `meta` traz `freshness_status`, `lag_days`,
`available_observed_dates` e `reference_basis`; `kpis` traz
`fresh_count`/`stale_count`/`historical_count`; a linha traz
`freshness_status`. Data histórica usa exatamente o dia pedido, sem fallback.
Nenhuma recusa ecoa a entrada. Nulo continua distinto de zero: os quatro
campos de checkout nulos, 35 linhas sem referência com diferença nula, 12
diferenças negativas preservadas com sinal.

## QA em navegador real — 51/51

Chrome do sistema via Playwright em venv isolado (sem tocar as dependências do
projeto), contra o **frontend e o backend de produção**, em 1440×900,
1024×768 e 390×844.

Aprovado: zero overflow horizontal nos três viewports; zero erro de console
relevante; zero *hydration warning*; seletor "Data observada" alimentado por
`available_observed_dates` com 37 opções e **nenhuma ≥ 08/09**; troca de data
refletida na URL nos dois sentidos; banner retrospectivo aparecendo e
desaparecendo pelo `aria-label`; a visão histórica **não** usa o vocabulário de
atraso; cartões comerciais com valor na visão histórica; troca rápida de datas
com a última escolha vencendo; filtro de marca preservando a data na URL; foco
por teclado alcançando elemento interativo com indicação visual; estado vazio
declarado com "ausência não é zero"; data inválida na URL ignorada em vez de
enviada ao backend.

O único 404 de console é o `favicon.ico` já registrado como dívida
pré-existente nesta mesma página.

## Achados desta rodada

1. **A frase-base é renderizada sem acentos.** `comparison_basis_text` foi
   escrita ASCII-only no backend: a tela mostra "Preco anunciado em 07/09/2026
   comparado a referencia sugerida ao consumidor (PDV) capturada em 02/09/2026.
   A origem nao declara a vigencia historica dessa referencia." É texto
   voltado ao usuário, em português, sem acentuação. Cosmético e visível;
   pendente de correção.
2. **Os cartões de qualidade de frescor aparecem com zero** quando não se
   aplicam ("Consulta retrospectiva: 0" no modo `latest`). É o mesmo
   comportamento do cartão de referência ambígua, que já existia — decisão de
   design, não defeito.
3. **Um teste apodrecia com o calendário.**
   `test_http_500_em_inconsistencia_de_serving_com_corpo_fixo` fixava
   `HOJE = 2026-09-03` para simular D0, mas a borda HTTP não injeta `today`.
   Escrito em 03/09 passava; de 04/09 em diante `HOJE` virou apenas uma data
   atrasada, o guarda *fail-closed* de D0 deixou de ser exercitado e o teste
   falhava para sempre. Corrigido em `e7fa7f9`: D0 vem do relógio real.

## Limitações que seguem abertas

1. **Shopee Ads sem `ad_spend`** de 28/08 a 07/09 — bloqueio de contrato, não
   ausência de dado. Depende de export com período terminando em D−1.
2. **ML produtos/ranking e Regional** sem teto superior nos jobs oficiais.
   Enquanto as fontes alcançarem D0, os dois seguem bloqueados.
3. **Silver/Gold Shopee** sem executor versionado.
4. **07/09 sem `barbours`** no PMA, irrecuperável por reprocesso.
5. `validity_status = missing` continua sendo a verdade da referência PDV: a
   comparação histórica é "preço daquele dia contra a referência de hoje", e
   **nunca** "a referência valia naquele dia".
6. Sem limiar comercial aprovado não há severidade: os únicos fatos seguem
   sendo `difference_amount` e `difference_pct`.

---

# Runbook operacional — publicação multicanal (Shopee e TikTok)

> **Gate PMA-2C3A-R.** O Mercado Livre **não** passa por aqui: ele continua em
> `marts.fact_marketplace_listing_price_daily`, publicado por
> `pipelines/sync_ml_listing_price_serving.py`.

## Precondições

A publicação **só é possível** quando as duas condições valem no Neon:

1. `alembic_version = 017` — a migration `017_create_fact_channel_offer_observation`
   aplicada;
2. `marts.fact_channel_offer_observation` existente.

`assert_apply_authorized` verifica **as duas** contra o banco real antes de
qualquer coisa. Um `stamp` manual sem DDL falha na segunda; uma tabela criada à
mão falha na primeira. O CLI não cria tabela em runtime — o schema pertence ao
Alembic.

Credenciais: `DATABASE_URL` (Neon) e `DATAMART_DATABASE_URL` (Data Mart). Nenhuma
variável nova.

## Comando de diagnóstico (read-only)

```bash
python -m pipelines.channel_offer_publisher --marketplace shopee
python -m pipelines.channel_offer_sync --marketplace tiktok
```

Não abre conexão de destino, **não adquire o advisory lock**, não escreve
auditoria e não toca na tabela de destino. Serve para conferir a fonte sem
disputar nada com quem está publicando.

## Comando de publicação

```bash
python -m pipelines.channel_offer_publisher --marketplace shopee --apply
```

Enquanto as feature flags estiverem desligadas, a carga-piloto exige comando
operacional **explícito**:

```bash
python -m pipelines.channel_offer_publisher --marketplace shopee --apply --operator-override
```

`--operator-override` é a única forma de publicar com a flag do canal desligada.
Ele não existe para uso rotineiro.

## Ordem que o comando executa

```
conexão de destino → precondição (017 + relação)
  → pg_try_advisory_lock(917120017), fail-fast
  → SÓ ENTÃO lê a fonte e classifica as contas
  → PublicationPlan imutável
  → valida account_watermark_at por marketplace e conta
  → DELETE por escopo + INSERT na MESMA transação
  → reconcilia ANTES do commit → commit → auditoria
  → libera o lock em TODOS os desfechos
```

## Exit codes

| código | desfecho | significado |
|---:|---|---|
| **0** | `published` | commit confirmado. Se a auditoria falhou depois, sai **0 mesmo assim** com aviso em stderr — os dados estão publicados |
| **1** | `rolled_back` | falha antes do commit; a transação foi desfeita e nenhuma fotografia mudou |
| **2** | `refused` | precondição ou plano recusou; **nenhuma linha foi tocada** |
| **3** | `lock_unavailable` | outra execução detém o lock; nada foi lido nem escrito |
| **4** | `indeterminate` | o commit foi tentado e levantou; o estado dos dados é **desconhecido** |
| **5** | uso | argumento inválido |

## Publicação indeterminada (exit 4)

O commit foi enviado e a conexão caiu. Isso é **indistinguível** de uma queda
antes do commit, então o estado dos dados não é conhecido.

O que o processo faz: mantém `audit.source_sync_run` em `running` com
`error_message` começando por `INDETERMINADO:`. **Não** marca `failed` — isso
afirmaria que nada foi gravado. **Não** tenta rollback — ele não desfaria um
commit possivelmente aplicado.

O que o operador faz:

1. anote o `sync_run_id` da mensagem;
2. leia a tabela para o escopo da execução:

```sql
SELECT observed_date, shop_account, count(*)
  FROM marts.fact_channel_offer_observation
 WHERE marketplace = 'shopee'
 GROUP BY observed_date, shop_account
 ORDER BY observed_date DESC;
```

3. se as linhas estão lá, o commit passou — feche o registro como `success`;
4. se não estão, o commit não passou — feche como `failed` e só então reexecute.

## Proibição de retry cego

**Nenhum desfecho é repetido automaticamente, e `indeterminate` nunca deve ser
reexecutado sem a leitura acima.** Repetir afirma que nada foi gravado — e é
exatamente isso que não se sabe. A guarda de regressão de watermark recusaria
uma reexecução mais antiga, mas ela não substitui a conferência: uma reexecução
com o mesmo watermark é aceita como rerun idempotente e sobrescreveria a
fotografia sem que ninguém tivesse verificado o que havia lá.

## Estados de conta

| estado | o que acontece com a fotografia anterior |
|---|---|
| saudável com ofertas | apagada e reposta |
| **saudável com zero ofertas** | **apagada**; `rows_loaded = 0`, execução bem-sucedida |
| indisponível | **preservada** — a conta nem entra nos escopos saudáveis |
| não executou | **preservada** — estava na fotografia publicada e sumiu da fonte |

Fonte indisponível **nunca** vira fotografia vazia, e não existe linha sentinela
representando "zero ofertas".

---

# Gate PMA-2C4A — serving multicanal de Shopee e TikTok (2026-09-16)

O piloto do PMA-2C3D publicou 1.900 ofertas em
`marts.fact_channel_offer_observation`, mas o endpoint não as exibia: o serviço
só consultava as tabelas legadas do Mercado Livre. Este gate ligou a leitura.

## Duas políticas de data, escolhidas pelo contrato da fonte

Não existe um teto global. Cada canal traz a sua política, e `meta.date_policy`
a declara no payload.

| | Mercado Livre | Shopee e TikTok |
|---|---|---|
| política | `closed_day` | `snapshot_current` |
| fonte | série diária | fotografia do estado corrente |
| teto consultável | **D−1** em America/Sao_Paulo | **dia operacional corrente (D0)** |
| D0 | inconsistência: recusado com 422, e uma linha em D0 faz o serving falhar fechado | caso **normal** |
| futuro | recusado | recusado |

A razão é o contrato de cada fonte. O ML é série diária: só um dia fechado
sustenta comparação, e uma linha em D0 só poderia vir de escrita fora do
contrato. Shopee e TikTok publicam fotografia derivada do watermark da conta no
próprio dia — para eles, exigir D−1 significaria nunca servir o que acabou de
ser publicado.

**D0 não é período fechado.** `meta.snapshot_mutability` vale
`mutable_operational_snapshot` enquanto a fotografia é do dia corrente: o número
é verdadeiro para o instante observado, não para o dia, e pode mudar hoje se a
origem recarregar. Um aviso em `meta.warnings` diz isso em português. Quando o
dia vira, passa a `settled_snapshot`.

**D0 permitido não é automaticamente `fresh`.** O frescor sai do watermark
materializado, não da igualdade de datas: se a fotografia do dia não tiver
nenhuma oferta com `snapshot_status = current`, a resposta vem `stale`. Por
linha, `freshness_status` traduz o `snapshot_status` que o publisher gravou —
na Shopee medimos 238 ofertas `current` e 454 `stale` dentro da mesma carga de
hoje, porque há modelo com carimbo de até 18 dias atrás.

## Fontes físicas

| canal | tabela de observação | tabela de referência |
|---|---|---|
| `ml` | `marts.fact_marketplace_listing_price_daily` | `marts.fact_suggested_price_reference_snapshot` |
| `shopee`, `tiktok` | `marts.fact_channel_offer_observation` | a mesma |

A tabela de referência é comum aos três porque a referência é do **produto**,
não do canal. As duas fatos **nunca aparecem na mesma consulta** — há teste de
contrato que varre o texto de todas as consultas e reprova se isso acontecer.
É a prova estrutural de que não existe fallback entre canais: um canal sem dado
devolve 200 com estado vazio tipado, nunca linha do outro.

O plano medido usa `idx_fcoo_escopo`:

```
Bitmap Heap Scan on fact_channel_offer_observation
  Recheck Cond: ((marketplace = 'shopee') AND (observed_date = '2026-09-16'))
  ->  Bitmap Index Scan on idx_fcoo_escopo
```

## Números reconciliados contra o Neon (snapshot de 2026-09-16)

| | Shopee | TikTok |
|---|---:|---:|
| `total_count` / `monitored_offers` | 692 | 1.208 |
| ativas / inativas | 593 / 99 | 909 / 299 |
| contas / marcas | 4 / 4 | 1 / 7 |
| fora do escopo de beleza | 0 | 223 |
| elegíveis | 308 | 350 |
| comparáveis | 150 | 102 |
| cobertura | 48,7% | 29,1% |
| `snapshot_status` | 238 current, 454 stale | 1.208 current |

API e SQL direto batem em todos esses campos, com `EXCEPT` bidirecional 0/0
entre as chaves paginadas e as da tabela.

## Semântica dos campos

Mapeamento explícito, sem inferência por nome:

| fato | payload |
|---|---|
| `offer_key` | `item_id` e `offer_key` (idênticos) |
| `observed_date` | `ref_date`, `observed_date` e `meta.observed_date` |
| `observed_price` | `advertised_price` — preço **anunciado** na vitrine |
| `observed_at` | `observed_at` — instante da observação da linha |
| `is_active` | `listing_status` (`active` / `inactive`) |
| `list_price` | `original_price` e `list_price` |
| `product_type` | `product_type` — **materializado**, nunca reclassificado |
| `account_watermark_at` | `account_watermark_at` e `meta.account_clocks[]` |
| `snapshot_status` | `snapshot_status` e `freshness_status` da linha |
| `business_scope` | `business_scope` e `meta.out_of_scope_offer_count` |

`product_type` vem do publisher, que decidiu com autoridades que a API não
alcança — flag nativa do canal, cadastro interno, BOM. Reclassificar aqui, com
apenas SKU e título, daria dois rótulos à mesma oferta conforme quem pergunta.

Não há `permalink`: a fato não guarda URL, e derivá-la do `offer_key` produziria
link quebrado com aparência de link bom.

## Denominadores

`monitored_offers` conta **todas** as ofertas do canal, inclusive as de marca
fora do escopo de beleza (Gocase e Denavita, no TikTok): elas existem e a
auditoria de cobertura precisa vê-las. O que elas não fazem é entrar em
`eligible_offers` nem na taxa de cobertura.

`eligible_offers` = ativas − `kit_confirmed` − `kit_suspected` − fora de escopo.
É `v2_product_type_aware`, e numerador e denominador saem da mesma versão.
`product_type_counts` conta as **ativas**, como no ML, e é o que mantém a
identidade `eligible = active − kits` aritmeticamente verdadeira.

O filtro `status` altera **somente a tabela**; KPIs e `metrics` descrevem sempre
o conjunto do filtro estrutural, preservando o denominador.

## Filtros novos

`shop_account` e `product_type` valem **apenas** para Shopee e TikTok. Pedidos
com `marketplace=ml` são recusados com 422 e mensagem fixa — nunca ignorados em
silêncio, que era o defeito do antigo `ref_date`.

A allowlist de `brand` passou a ser por canal: o ML mantém as quatro marcas
publicadas; os canais aceitam as cinco de `BEAUTY_SCOPE_BRANDS`. Marca fora de
escopo (`gocase`, `denavita`) aparece nas linhas mas não é filtrável — limitação
conhecida.

## Feature flags

`PMA_SHOPEE_ENABLED` e `PMA_TIKTOK_ENABLED` continuam **desligadas por padrão**,
e este gate não tocou a configuração do Render nem da Vercel. Com a flag
desligada nenhuma consulta é emitida e a resposta é 200 com KPIs zerados e
`coverage_rate` nulo. Com a flag ligada, o dado real é servido e não há fallback
para o ML.

## Limitações abertas

- **O frontend ainda não consome estes canais.** `monitoramento-preco-contract.ts`
  tipa `meta.marketplace` como `"ml"` e `advertised_price` como `number`. O
  schema da API passou a admitir `advertised_price` nulo (a fato permite, e o ML
  nunca produz nulo), então nenhuma resposta de ML muda — mas exibir Shopee ou
  TikTok exige um gate de frontend.
- **`observed_date` histórico ainda não existe para os canais**: há uma única
  fotografia publicada. `available_observed_dates` cresce conforme o publisher
  rodar.
- **A publicação continua manual.** Não há Scheduler nem Airflow chamando o
  `channel_offer_publisher`; cada fotografia veio de execução operacional
  explícita.
- **Preço nulo em oferta ativa** cai em `comparison_status = no_reference` com
  `non_comparable_reason = invalid_channel_price`, porque a partição comercial
  tem cinco valores congelados por teste e tipados no frontend. Hoje o caso não
  ocorre: as 8 ofertas sem preço do TikTok são todas inativas.

---

# Gate PMA-2C4B — frontend multicanal (2026-09-16)

O backend passou a servir Shopee e TikTok no PMA-2C4A, mas a tela só entendia
`ml`: `meta.marketplace` era tipado como literal `"ml"` e `advertised_price`
como `number`. Este gate adaptou `/monitoramento-preco` aos três contratos.

## Seletor de canal e ativação coordenada

O seletor só aparece quando há mais de um canal **disponível**, e disponível
depende de **duas** chaves independentes:

| | quem decide | efeito de ligar sozinho |
|---|---|---|
| `PMA_SHOPEE_ENABLED` / `PMA_TIKTOK_ENABLED` | backend | não cria botão nenhum |
| `NEXT_PUBLIC_PMA_SHOPEE_ENABLED` / `NEXT_PUBLIC_PMA_TIKTOK_ENABLED` | frontend | botão que devolve envelope `unavailable` |

As quatro nascem **desligadas**, e a ausência da variável resolve `false` — só a
string exata `"true"` liga. A ativação é **coordenada de propósito**: nenhuma
das duas sozinha expõe o canal. Este gate não tocou `.env`, Render nem Vercel.

Com tudo desligado — o estado de produção — a tela é exatamente a de antes:
Mercado Livre, sem seletor, sem coluna de tipo, sem filtro de conta.

## Duas políticas de data na tela

| | Mercado Livre | Shopee e TikTok |
|---|---|---|
| teto exibido | `até DD/MM (D−1)` | `até DD/MM (hoje)` |
| D0 | recusado | permitido |
| aviso | nenhum | *"Fotografia do dia corrente … AINDA PODE MUDAR hoje se a origem recarregar. Não é período fechado nem contagem definitiva do dia."* |

A frase de D−1 **nunca** é reutilizada nos canais novos — há teste que varre o
texto renderizado e reprova se `D−1` aparecer numa resposta `snapshot_current`.

## Frescor em dois níveis

A tela distingue quatro situações, e a da linha é o **pior** dos dois níveis:

| situação | quando | o que a linha diz |
|---|---|---|
| Revista nesta fotografia | fotografia em dia, oferta `current` | nada extra |
| Não revista nesta fotografia | fotografia em dia, oferta `stale` | o preço é o da última vez em que foi vista — **não** manda verificar o sync |
| Fotografia atrasada | fotografia `stale` | atraso real, em dias |
| Consulta retrospectiva | dia escolhido | não é atraso |

Na Shopee de 2026-09-16 são 238 ofertas revistas e 454 não revistas dentro da
mesma carga de hoje — há modelo com carimbo de até 18 dias.

## Preço ausente

`advertised_price` nulo vira **"Não observado"**, nunca `R$ 0,00`. A diferença
não é calculada (a API já a manda nula) e a linha não é classificada como
abaixo nem acima da referência. Medido no QA: zero células de preço anunciado
exibindo `R$ 0,00`. Um `R$ 0,00` na coluna **Diferença** é medição legítima —
preço anunciado igual ao sugerido — e continua sendo exibido.

## Cobertura por conta

Sob `snapshot_current`, o bloco de contexto lista cada conta com ofertas,
quantas foram revistas e o horário da carga, direto de `meta.account_clocks`.
Nada é somado na tela. Ofertas de marca fora do escopo de beleza aparecem na
tabela e são declaradas em `meta.out_of_scope_offer_count`: não entram em
`eligible_offers` nem na cobertura.

## Kits

`product_type` vem **materializado** do publisher e é apenas traduzido:
kit confirmado · possível kit · sem sinal de kit · sinal de kit desconhecido.
O frontend **não reclassifica kit**. `product_type_unknown` é rotulado como
"sinal de kit desconhecido", jamais como "produto simples".

## Links externos

Allowlist **por marketplace**, sempre HTTPS, com `noopener noreferrer`. Um
permalink de Shopee numa linha de ML não vira link. A fato dos canais não
guarda URL, então hoje Shopee e TikTok mostram texto sem link — e a URL
**nunca** é construída a partir de `offer_key`.

## QA medido (Chromium, backend local read-only)

| viewport | overflow | alvo < 44px | fonte mínima | linhas |
|---|---:|---:|---:|---:|
| 1440×900 | 0px | 0 | 12px | 500 |
| 1024×768 | 0px | 0 | 12px | 500 |
| 390×844 | 0px | 0 | 12px | 500 |

Medições **restritas ao `<main>`**: a navegação lateral do shell tem alvos de
36px e rótulos de 9–10px, condição pré-existente e fora desta tela. Único erro
de console: `404 /favicon.ico`, que o projeto não publica desde antes deste
gate.

Na troca de canal, o instante imediato mostra **0 linhas** sob o título do
canal novo — nenhum dado do canal anterior sobrevive à troca.

## Limitações

- **Os canais continuam desligados em produção.** Este gate não ativou nada.
- `available_observed_dates` dos canais tem uma única data: só há uma
  fotografia publicada. A lista cresce conforme o publisher rodar.
- Marca fora do escopo (`gocase`, `denavita`) aparece nas linhas mas não é
  filtrável — a allowlist de `brand` cobre as cinco marcas de beleza.
- A publicação continua manual: não há Scheduler chamando o publisher.

## Revisão terminal (PMA-2C4B-R/V)

**Marca fora do escopo — regra definida.** Gocase e Denavita são *formalmente*
fora do escopo: o backend declara por linha (`business_scope`) e em agregado
(`meta.out_of_scope_offer_count`), e já as exclui de `eligible_offers` e da
cobertura. A tela passou a:

- **rotular** cada linha com o chip "Fora do escopo", na tabela e no diálogo —
  antes elas apareciam sem qualquer indicação, e 223 ofertas de fora do produto
  podiam ser lidas como monitoramento de beleza;
- oferecer um filtro de **Escopo** ("Todas as marcas da fotografia" /
  "Somente marcas monitoradas"), traduzido para a lista de marcas que a própria
  API declara monitoradas — filtro **do servidor**, então total e paginação
  continuam corretos. Medido: 1.208 → 985.

Limitação registrada: isolar *somente* as marcas fora do escopo exigiria que o
backend aceitasse `gocase`/`denavita` em `brand`, hoje recusadas com 422. É
mudança de backend e ficou fora deste PR de frontend.

**Filtro de tipo.** Passou a oferecer os quatro estados sempre.
`product_type_counts` conta apenas as **ativas**, e filtrar as opções por ele
esconderia do filtro um tipo existente só entre as inativas — visível na coluna
e inalcançável.

**Paridade do Mercado Livre.** A comparação controle × branch nos três viewports
mostrou quatro mudanças indevidas na tela do ML, todas revertidas: o subtítulo
publicado, o chip de teto de data, o sublabel de frescor por linha e uma
concordância errada ("de Mercado Livre"). Após a correção, colunas, filtros,
linhas, primeira linha e overflow são **idênticos** ao controle nos três
viewports, e a única diferença de texto é o título dinâmico.

**Diálogo — lacuna pré-existente.** O diálogo de detalhe não prende o foco nem
fecha com Escape. Medido idêntico no controle (`origin/main`, só ML), portanto
não é regressão deste PR; na branch o foco ao menos volta para quem abriu, o
que no controle não acontece. Fica registrado para um gate de acessibilidade.

## Diálogo: a contradição PMA-4F × PMA-2C4B, resolvida (PMA-2C4B-H1)

**Não houve regressão. A medição do PMA-2C4B é que estava errada.**

`MobileDrawer` renderiza `role="dialog"` no shell **mesmo fechado** — só com
`aria-hidden="true"` — e vem antes no DOM do portal do `KpiDrilldownDialog`.
Um `document.querySelector('[role="dialog"]')` devolve o **drawer**, não o
diálogo. Daí os dois sintomas falsos: "o foco não está dentro" (estava, mas
dentro do outro nó) e "Escape não fechou" (fechou, mas o seletor continuava
achando o drawer).

Medido em Chromium real, com o alvo desambiguado (`role="dialog"` que **não**
está `aria-hidden`), nos três canais e nos três viewports:

| verificação | ml | shopee | tiktok |
|---|---|---|---|
| `role` + `aria-modal` | ok | ok | ok |
| nome acessível ligado ao título | ok | ok | ok |
| foco inicial dentro | ok | ok | ok |
| fundo com `inert` | ok | ok | ok |
| Tab não escapa / dá a volta | ok | ok | ok |
| Shift+Tab não escapa | ok | ok | ok |
| Escape fecha + foco volta | ok | ok | ok |
| botão fecha + foco volta | ok | ok | ok |

O Mercado Livre tem dois focáveis ("Fechar detalhes" e "Abrir anúncio") e o
ciclo de Tab fecha entre eles; Shopee e TikTok têm um só, porque a fato dos
canais não guarda URL.

**Histórico.** O componente tem dois commits: `8150408` (2026-07-24), que já
nasceu com Escape, `shiftKey` e `previousFocusRef`, e `aa6e245` (2026-08-25),
que **acrescentou** preservação de foco na navegação interna. A garantia nunca
foi removida.

**O que mudou neste gate.** A regra do trap saiu de dentro do `.tsx` para
`src/lib/focus-trap.ts` — o type-stripping do Node não processa JSX, então
antes ela só era verificável abrindo um navegador. Em `.ts` puro virou teste no
mesmo runner dos demais, sem dependência nova. O comportamento é idêntico,
reconferido em Chromium depois da extração.

**O drawer fechado não é armadilha de teclado**: tem 14 focáveis no markup, mas
0 alcançáveis (`offsetParent === null`) — fora da ordem de tabulação.

## A barreira do `--apply` e a data da CLI (PMA-2C4D1-H1)

A publicação autorizada de 17/09/2026 foi recusada por dois defeitos no
publisher. Nenhum deles tinha a ver com os dados — a fotografia candidata
estava correta e continua pendente de publicação.

### `alembic_version` guarda o head, não o histórico

`assert_apply_authorized` exigia que `"017"` fosse **uma das linhas** de
`alembic_version`. Essa tabela guarda somente a revisão corrente. Quando a 018
(Expedição) e a 019 (Shopee FBS) entraram, o head virou `"019"` e a 017 — que
nunca foi revertida e continua aplicada — sumiu da tabela. A barreira passou a
recusar para sempre, com a mensagem de que "a migration ainda não foi
aplicada".

A guarda agora pergunta se a 017 é **ancestral** do head, percorrendo o grafo
real de `down_revision`. Aceita head 017, 018, 019 e qualquer descendente
futuro; recusa head anterior à 017, revisão desconhecida, `alembic_version`
vazia ou ausente, mais de um head, ciclo, `down_revision` não linear e falha
ao ler o grafo. Não há comparação lexical nem numérica de identificadores: que
"018" seja maior que "017" não prova parentesco.

O grafo é lido **dos arquivos versionados**, por `ast`, e não via
`alembic.script.ScriptDirectory`. O motivo é concreto: o módulo põe `apps/api`
no `sys.path`, e ali existe `apps/api/alembic/` com `__init__.py`. Dentro do
processo do publisher, `import alembic` resolve para esse diretório e sombreia
o pacote instalado — `alembic.script` e `alembic.config` deixam de existir.
Foi medido. Ler `down_revision` dos próprios arquivos usa o mesmo dado, sem
import, sem dependência nova e sem executar a migration.

**A prova física continua obrigatória** e independente: `to_regclass` sobre um
identificador constante e versionado. Um `stamp` manual passaria na primeira
prova sem criar a tabela; uma tabela criada à mão passaria na segunda sem estar
sob o Alembic. As duas juntas, sempre.

### `--observed-date` comparava texto com data

O argparse entregava `str` e o driver devolve `datetime.date`;
`tiktok_snapshot_exists` comparava os dois com `==`. Uma data existente era
recusada como inexistente. A normalização agora acontece **na fronteira da
CLI**, com `type=`, usando `strptime("%Y-%m-%d")` — e não `date.fromisoformat`,
que a partir do 3.11 aceitaria `20260917` e até data com hora. Formato inválido
derruba o parser antes de qualquer conexão, lock ou auditoria, com mensagem
constante que não ecoa o que foi digitado. Sem a flag, o comportamento canônico
é o de antes. A Shopee segue ignorando o parâmetro: o contrato dela é
`snapshot_current`, sem série por dia.

### O que este hotfix NÃO fez

Nenhuma fotografia foi publicada. A de 17/09 continua pendente de autorização
e de uma nova execução controlada. Nenhuma flag foi ligada, nenhuma migration
criada ou alterada, nenhum dado escrito.
