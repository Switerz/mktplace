# Estoque Full do Mercado Livre: contrato dos indicadores (dry-run)

**Estado: dry-run, sem escrita.** O pipeline `pipelines/sync_ml_full_stock_daily.py` só lê e imprime agregados. Ele não tem `--apply` e não tem tabela de destino. Criar a fato no Neon exige migration própria. Migration, publicação e flag são gates separados.

## Fonte

Tabelas `raw.ml_full_inventory_snapshot`, `raw.ml_full_inventory_unavailable` e `raw.ml_full_inventory_listing` no Data Mart. Quem as publica é a DAG `mercadolivre_full_stock_snapshot` do airflow (goca-se/airflow#2057). A DDL delas **ainda não foi aplicada**; enquanto as tabelas não existirem, o pipeline responde `fonte_inexistente`.

As vendas seguem o mesmo contrato do `sync_ml_fulfillment_daily`:
- `api.ml_orders` com `status='paid'`, datado por `date_created`;
- envio Full pelo `logistic_type` da versão mais recente do shipment;
- unidades de `api.ml_order_line_items`, por (marca, item, variação).

## Regras

| Regra | Medição que a sustenta (16/10/2026) |
|---|---|
| O grão é o **inventário** (marca, conta, `inventory_id`). Estoque nunca é somado por anúncio. | 74,5% das unidades Full vendidas em 28 dias vieram de anúncios que dividem inventário |
| A velocidade do inventário soma **todos** os anúncios e variações ligados a ele, inclusive inativos e não-Full | Rituaria: 29,8% das unidades vieram de anúncio hoje inativo ou não-Full |
| Uma venda sem vínculo único **não é rateada**: aparece em `unidades_28d_nao_alocadas` e em `cobertura_da_alocacao` | Ratear esconderia o buraco da ponte |
| `inventory_id` não prova Full. Só entra na operação o inventário com pelo menos um anúncio `fulfillment`; os demais ficam como `FORA_DO_FULL` | Um anúncio `cross_docking` tinha `inventory_id` |
| Leitura sem OK vira `SEM_LEITURA`, com quantidades nulas. Marca sem fotografia vira `sem_fotografia_no_dia`. Nada vira zero. | — |
| `transfer` é movimento **interno** do ML e não é remessa do operador (Eli) para o Full | A remessa vem da NF do Bling e é outro fato |

## Indicadores

- **Velocidade:** `unidades_28d` na janela `[ref_date − 28, ref_date)`, em dias completos.
- **Cobertura:** `cobertura_torre_dias = disponível ÷ (unidades_28d ÷ 28)`. É nula quando não há demanda.
- **Classificação**, avaliada nesta ordem:
  1. `SEM_GIRO_CANDIDATO` ou `SEM_DEMANDA_MEDIDA`, quando não houve demanda;
  2. `RUPTURA_CANDIDATA`, quando há demanda e o disponível é zero;
  3. `BAIXO_CANDIDATO`, com cobertura menor que 7 dias;
  4. `EXCESSO_CANDIDATO`, com cobertura de 90 dias ou mais;
  5. `SUFICIENTE`, nos demais casos.
- **Os limites 7 e 90 são PROVISÓRIOS.** Vêm da Shopee FBS e nenhum responsável os ratificou para o ML. Eles saem no resumo marcados como provisórios.

## Saída

Somente agregados por marca: inventários, inventários compartilhados, disponível, indisponível e `transfer` do Full, contagem por classe, unidades alocadas, unidades não alocadas por motivo e cobertura da alocação. Nenhum id de inventário, anúncio ou conta é impresso.
