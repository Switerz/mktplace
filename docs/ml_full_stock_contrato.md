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

## Estado por marca na API e na tela (T-0051)

`GET /api/v1/performance/ml-full-estoque` devolve `marcas[].estado` e distingue três situações. Elas não podem ser confundidas.

| Situação | Como chega ao mart (029) | API | Tela | Total |
|---|---|---|---|---|
| **Sem estoque Full**: a enumeração foi completa e deu zero | Linha em `fact_ml_full_stock_brand_daily` com `inventarios = 0`, `inventarios_full_lidos = 0`, `inventarios_compartilhados = 0`, `disponivel_full`, `nao_disponivel_full` e `transfer_full` **NULL**, `source_run_id` não vazio e `published_at` presente. **Nenhuma** linha em `fact_ml_full_stock_inventory_daily` nem em `fact_ml_full_stock_status_daily` no dia. Não há `captured_at`. O 0 da API vem da regra de contrato, não da coluna, que continua NULL. | `estado = "sem_estoque_full"`, `tem_fotografia_no_dia = true`, `disponivel_full = 0` (e não disponível e transferência 0). A marca fica em `marcas_com_fotografia`. | "Sem estoque Full", em tom neutro | Conta 0 e **não** torna o total parcial |
| **Saldo disponível 0**: há inventário, mas nada disponível | Linhas de inventário com `disponivel = 0`; na marca, `disponivel_full = 0` e `inventarios_full_lidos > 0` | `estado = "com_inventario"`, `disponivel_full = 0` e `nao_disponivel_full` medido | "0" com "saldo disponível 0 · N não disponível" | Soma normal |
| **Ausência ou falha**: não coletada, não publicada ou stale | Nenhuma linha da marca no dia | `estado = null`, números `null`; a marca fica em `cobertura.marcas_sem_fotografia` | "—" e chip âmbar | **Parcial** |

**Regras**
- "Sem estoque Full" **não** quer dizer "não opera Full". Significa apenas que a fotografia do dia não encontrou nenhum inventário.
- A 029 já admite a linha zero **sem migration**:
  - `inventarios >= 0`;
  - `ck_fmlfsb_disponivel_exige_leitura`: `(inventarios_full_lidos > 0) = (disponivel_full IS NOT NULL)`. Com 0 e NULL, a comparação fica `FALSE = FALSE`, e o CHECK passa;
  - `nao_disponivel_full` e `transfer_full` só exigem `>= 0` quando preenchidos. O contrato da API exige NULL nesses campos;
  - `source_run_id` e `published_at` são NOT NULL.
- Toda outra divergência entre marca e inventário continua sendo **contrato quebrado (503)**. Exemplos:
  - a marca diz N > 0 inventários e não há nenhuma linha;
  - a marca diz 0 e há linhas de inventário ou de status;
  - a marca diz 0, mas tem lidos, compartilhados ou alguma quantidade preenchida (inclusive 0 em vez de NULL);
  - a marca diz 0 e não tem linhagem.
- **Total do escopo:**
  - Se todas as marcas esperadas têm fotografia, as marcas "sem estoque Full" entram com 0 e o total não é parcial. Se todas estão sem estoque Full, o total é 0 e a tela mostra "Sem estoque Full".
  - Se falta qualquer marca, o total é **parcial**: é a soma do que foi medido, com o motivo "Sem fotografia no dia: …". A marca ausente nunca vira zero.
- **Na web:** um `estado` desconhecido, ou incoerente com a fotografia, é erro de contrato e a tela não mostra números. Um exemplo de incoerência: `sem_estoque_full` com `disponivel_full` diferente de 0. Um payload sem o campo, vindo de uma API anterior, é lido como antes: com fotografia, `com_inventario`; sem fotografia, ausência.
- **Quem grava a linha zero:** o publicador ainda **não** grava. Hoje ele recusa a marca sem linhas. Até isso mudar (gap Z2 do QA da T-0044b), o estado não aparece em produção.

## Saída

Somente agregados por marca: inventários, inventários compartilhados, disponível, indisponível e `transfer` do Full, contagem por classe, unidades alocadas, unidades não alocadas por motivo e cobertura da alocação. Nenhum id de inventário, anúncio ou conta é impresso.
