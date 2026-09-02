# Monitoramento de preços próprios — estado real

**Última atualização:** 2026-09-02 (Gate PMA-3/4)

> Este documento separa deliberadamente **código versionado**, **migration
> aplicada**, **dado publicado**, **backend publicado**, **frontend publicado** e
> **smoke**. Cada linha diz o que de fato aconteceu — não o que está pronto para
> acontecer.

## Estado por camada

| Camada | Estado | Evidência |
|---|---|---|
| Código versionado | ✅ `7bd81e7` em `main` | migration 014, sync, importador, endpoint, tela, 58 testes de tela |
| Migration no Neon | ✅ **aplicada**, `alembic_version = 014` | 012 → 014 numa execução |
| Dado ML publicado | ✅ **25.559 linhas** | janela 2026-08-03..2026-09-01 |
| Referência B2B publicada | ✅ **221 linhas**, 1 snapshot | 5 marcas |
| Endpoint reconciliado | ✅ contra o Neon real | KPIs fecham em 855; conferido linha a linha |
| Tela `/monitoramento-preco` | ✅ **implementada** | 1377/1377 testes, typecheck limpo, build 8,18 kB |
| Frontend publicado na Vercel | ✅ **publicado** | `mktplace-gobeaute.vercel.app/monitoramento-preco` → HTTP 200 |
| Backend publicado no Render | ❌ **NÃO PUBLICADO** | rota ausente do `openapi.json` de produção |
| Smoke de produção | ⚠️ **parcial** | frontend responde; a chamada à API 404 porque o Render está defasado |
| QA em navegador real | ❌ **NÃO EXECUTADO** | sem driver de navegador na sessão |
| Automação (Scheduler) | ❌ **NÃO EXISTE** | sync 100% manual |

### Bloqueio de produção — leia antes de usar a tela

A tela está publicada na Vercel, mas **o backend em
`https://mktplace-api.onrender.com` roda uma revisão anterior** e não expõe
`/api/v1/performance/monitoramento-preco` (confirmado no `openapi.json` de
produção: a rota não está lá). O build da Vercel aponta para esse host, então
em produção a tela renderiza **o estado de erro**, não os dados.

Não houve acesso real ao Render nesta sessão: nenhum token, nenhuma CLI
(`render` ausente do PATH), nenhum deploy hook no `.env` e nenhum
`render.yaml` no repositório — o serviço é configurado pelo painel. O deploy
do backend é, portanto, **READY FOR OWNER DEPLOY**: publicar a revisão
`7bd81e7` (o endpoint já estava em `e5322d0`; qualquer revisão de `main` a
partir dela serve) e reexecutar o smoke.

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

## QA — o que foi e o que não foi verificado

**Verificado com backend local ligado ao Neon em leitura (porta 8099) e
frontend do build final (`next start`, porta 3100):**

- `/monitoramento-preco` → HTTP 200; shell servido com `lang="pt-BR"`.
- Reconciliação API × Neon, em D−1 = 2026-09-01:
  `monitored_count` 855 = 855 linhas no Neon; `inactive_count` 194 = 194
  não-ativos; 661 ativos = 138 comparáveis + 523 sem referência + 0 ambíguos;
  soma dos seis status = 855. Por marca: barbours 309, kokeshi 219, lescent 145,
  rituária 182 — idênticas em API e banco.
- Uma linha `below_reference` conferida campo a campo contra o banco, incluindo
  `difference_amount` = anunciado − sugerido (−90,10 conferido).
- Paginação real: 500 + 355 = 855, 855 `item_id` distintos, sem repetição.
- Filtros: `brand=rituaria` → 182; `status=below_reference` → tabela 18 e KPI
  ainda 855 (denominador preservado); busca sem resultado → 0.
- `ref_date` recusado com 422 **sem ecoar a entrada**, inclusive para
  `<script>x</script>`.
- Nenhum `seller_id` no payload; os quatro campos de composição vêm `NULL`.
- Alvos de toque `min-h-[44px]` e `focus-visible:ring-2` em todos os selects,
  input e botões; nenhum texto abaixo de 12px (`text-xs` é o piso, zero
  `text-[NNpx]`); tabela larga dentro de `TableScrollHint`; drill-down reusa
  `KpiDrilldownDialog`, que tem `role="dialog"`, `aria-modal`, trap de Tab,
  Escape, retorno de foco e lock de scroll do body.
- 1377/1377 testes (58 novos), `tsc --noEmit` limpo, build `○
  /monitoramento-preco 8,18 kB / 117 kB`.

**NÃO verificado — não havia driver de navegador nesta sessão** (o Playwright
MCP não está autorizado e a sessão é não interativa, logo não há como rodar o
fluxo de OAuth). Fica pendente, e **não deve ser declarado aprovado**:

- renderização real nos viewports 1440×900, 1024×768 e 390×844;
- ausência de overflow horizontal medida em layout renderizado;
- `console error` e `hydration warning` em execução real;
- comportamento de teclado do diálogo exercitado de fato (trap, Escape, retorno
  de foco) — só a existência do código foi conferida;
- estados `stale_observation`, `non_comparable_reference_ambiguous` e vazio,
  que hoje não ocorrem naturalmente no dado (0 linhas cada) e cuja checagem
  ficou restrita a teste unitário, sem interceptação de rede.

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

## Próximo gate

1. **Publicar o backend no Render** (revisão `7bd81e7`) e reexecutar o smoke —
   é o único bloqueio para a tela ficar utilizável em produção.
2. **QA em navegador real** nos três viewports, com `console` e hidratação
   observados, cobrindo os estados que hoje não têm dado.
3. **Resolver as 3 linhas ambíguas da Rituária** na tabela de origem — decisão
   de Trade/pricing, não de engenharia.
4. Só depois: avaliar agendamento do sync, com observação de duas execuções
   manuais consecutivas com `EXCEPT` (0,0) antes de qualquer automação.
