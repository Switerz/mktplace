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
