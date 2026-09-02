# Monitoramento de preços próprios — estado real

**Última atualização:** 2026-09-02 (Gate PMA-2R)

> Este documento separa deliberadamente **código versionado**, **migration
> aplicada**, **dado publicado**, **backend publicado**, **frontend publicado** e
> **smoke**. Cada linha diz o que de fato aconteceu — não o que está pronto para
> acontecer.

## Estado por camada

| Camada | Estado | Evidência |
|---|---|---|
| Código versionado | ✅ `e5322d0` em `main` | migration 014, sync, importador, endpoint, testes |
| Migration no Neon | ✅ **aplicada**, `alembic_version = 014` | 012 → 014 numa execução |
| Dado ML publicado | ✅ **25.559 linhas** | janela 2026-08-03..2026-09-01 |
| Referência B2B publicada | ✅ **221 linhas**, 1 snapshot | 5 marcas |
| Endpoint reconciliado | ✅ contra o Neon real | KPIs fecham em 855 |
| Tela `/monitoramento-preco` | ❌ **NÃO EXISTE** | não implementada |
| Backend publicado no Render | ❌ **NÃO PUBLICADO** nesta rodada | — |
| Frontend publicado na Vercel | ❌ **NÃO PUBLICADO** | não há tela |
| Smoke de produção | ❌ **NÃO EXECUTADO** | depende dos deploys |
| Automação (Scheduler) | ❌ **NÃO EXISTE** | sync 100% manual |

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

## Próximo gate — PMA-3

1. Implementar a tela `/monitoramento-preco` sobre o contrato já reconciliado
   (nenhum endpoint novo é necessário).
2. QA nos três viewports (1440×900, 1024×768, 390×844) e acessibilidade.
3. Deploy do backend no Render e do frontend na Vercel.
4. Smoke de produção com reconciliação dirigida contra o Neon.
5. Só depois: avaliar agendamento do sync, com observação de duas execuções
   manuais consecutivas com `EXCEPT` (0,0) antes de qualquer automação.
