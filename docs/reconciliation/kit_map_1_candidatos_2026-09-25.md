# Diagnóstico de pontes de kit — KITS-MAP-1

Proveniência e limites de
[`kit_map_1_candidatos_2026-09-25.csv`](kit_map_1_candidatos_2026-09-25.csv):
927 linhas, uma por oferta com sinal de kit.

> **Este arquivo é analítico. Nenhum runtime o consome.** Ele carrega
> `CANDIDATE_REVIEW`, que nasce de composição reconstruída de nota fiscal.
> A configuração de produção é reconstruída do banco em
> [`apps/api/tools/reconcile_kits_map_2.py`](../../apps/api/tools/reconcile_kits_map_2.py),
> não deste CSV. Ver [`docs/contrato_bom_kits.md`](../contrato_bom_kits.md).

## Data da fotografia

**2026-09-25.** Todas as três fontes foram lidas no mesmo dia:

| fonte | banco | recorte |
|---|---|---|
| `marts.fact_channel_offer_observation` | Neon | `max(observed_date)` por canal = **2026-09-25** nos dois canais |
| `marts.fact_suggested_price_reference_snapshot` | Neon | snapshot mais recente por `captured_at` — **247 linhas** |
| `raw.protheus_kit_components` | Data Mart | `last_seen_at` máx. 2026-09-25 18:00 UTC — **4.691 linhas** |
| `gold.dim_produto_gobeauty`, `gold.map_produto_codigo_gobeauty`, `silver.gobeaute_produto_cadastro` | Data Mart | leitura direta |
| `silver.bling_nfes`, `silver.stg_shopee_order_items`, `raw.bling_pedidos_*` | Data Mart | composição empírica |

O Mercado Livre **não** está na fotografia: `fact_channel_offer_observation`
só tem `shopee` e `tiktok`. Coerente com a auditoria PMA-REF-LINK-1, que não
encontrou kit sem composição no ML.

## Universo

927 ofertas com `product_type ∈ {kit_confirmed, kit_suspected}` — Shopee 355,
TikTok 572; 397 `seller_sku` distintos; 9 ofertas sem `seller_sku`.

## Critérios de classificação

Exatamente um `status_ponte` por oferta, por precedência fixa:

1. `EXACT_DIRECT` — SKU do canal é, ele próprio, chave de kit na BOM, ou
   `(marca, código)` alcança um nó com `codigo_protheus` único;
2. `EXACT_ALIAS` — `codigo_bling` / `codigo_tiny` / `codigo_shopify` /
   `codigo_omie` / `sku_antigo` alcança um único SKU Protheus;
3. `CANDIDATE_REVIEW` — evidência, não fato: casamento por EAN de consumidor,
   ou composição reconstruída de NF;
4. `AMBIGUOUS` — mais de um candidato. Nunca desempatado;
5. `CROSS_BRAND_CONFLICT` — marca da oferta diverge da marca do kit ou dos
   componentes;
6. `UNMAPPED` — nenhuma ponte.

Nenhuma promoção por título, distância textual, preço ou EAN *placeholder*
(prefixos `0000000000`, `2000000000`, `7890000000`).

## Resultado

| status | Shopee | TikTok | total |
|---|---:|---:|---:|
| EXACT_DIRECT | 0 | 0 | 0 |
| EXACT_ALIAS | 0 | 10 | 10 |
| CANDIDATE_REVIEW | 247 | 324 | 571 |
| AMBIGUOUS | 0 | 6 | 6 |
| CROSS_BRAND_CONFLICT | 0 | 7 | 7 |
| UNMAPPED | 108 | 225 | 333 |

## Limites que o arquivo não resolve

- **A composição empírica não foi validada contra o Protheus.** Nos 10 kits em
  que as duas composições existem, apenas 1 bate integralmente. As demais
  divergências são de **geração de código** — `KS03042` e `KS03011` são o mesmo
  "Creme Gel Facial Pele Plena" com EANs diferentes —, mas isso é uma leitura,
  não uma prova. É por isso que as 571 linhas `CANDIDATE_REVIEW` estão
  bloqueadas.
- **Uma das duas rotas empíricas está congelada.** `raw.bling_pedidos_*` para
  em 2025-11-30 e `bling_pedidos_apice` está vazia. A rota por
  `stg_shopee_order_items` → `silver.bling_nfes` é fresca (24/09).
- **`kit_available_units` no CSV é pouco confiável** e foi recalculado no
  KITS-MAP-2. A coluna original usava `raw.webgex_posicao_estoque_*`, que **não
  é posição de estoque**: 100% das 10.139 linhas têm `status = 'PENDENTE'`.

## PII

**Zero.** O CSV tem canal, marca, identificadores de anúncio, SKU do vendedor
e métricas derivadas. Nenhuma coluna de comprador, pedido, endereço, documento
ou contato. As consultas que tocaram `silver.bling_nfes` e
`raw.notas_fiscais_unificadas_protheus_tiny_bling` — tabelas que **têm** PII —
selecionaram apenas `item_codigo`, `item_quantidade`, `data_emissao` e o número
do pedido da loja, e nada disso foi agregado ao arquivo.
