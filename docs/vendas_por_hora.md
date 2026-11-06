# Vendas por hora — o "Hoje" padronizado da Gerencial

Com o período **Hoje**, a Gerencial ganha um bloco **"Hoje até 13h"**:

- **Corte comum:** GMV, pedidos e ticket de ML, TikTok e Shopee, todos contados até a mesma hora cheia.
- **Comparação:** os mesmos números de **ontem** e do **mesmo dia da semana passada**, até a mesma hora.
- **Por canal:** uma tabela com o GMV de cada canal.

## Por que um corte só

A fato diária guarda um número por dia, e a DAG do dia corrente o reescreve de hora em hora, então o "até 12h" se perde quando chega o "até 13h". Além disso, cada canal chega com um atraso diferente (ML ~:30, TikTok ~:35, Shopee ~:15).

O corte comum resolve as duas coisas. Ele é a **menor hora fechada** entre os canais do filtro: se a Shopee só chegou até 11h, ninguém passa de 11h, e o bloco diz quem está segurando o corte.

## Peças

| Peça | O que faz |
|---|---|
| `marts.fact_marketplace_hourly_sales` (migration 039) | Dia BRT × hora BRT × canal × loja, com GMV e pedidos. Só horas fechadas. |
| `marts.fact_marketplace_hourly_sales_fonte` | Uma linha por canal: até quando a fonte comprovadamente tem dado. |
| `pipelines.sync_vendas_por_hora` | Lê o Data Mart pedido a pedido e regrava os últimos 8 dias. |
| DAG `marketplace_tower_dia_corrente` (Airflow) | Chama o módulo de hora em hora, no minuto 40. |
| `GET /api/v1/performance/hoje-por-hora` | Calcula o corte e soma hoje, ontem e a semana passada. |
| `HojeAteAHora` (web) | Mostra o bloco quando o período é "Hoje". |

## Regras (as mesmas da fato diária)

- **ML:** `api.ml_orders`, `status = 'paid'`, `total_amount`. ⚠️ `date_created` está em **UTC−4**, o fuso do Mercado Livre: o módulo soma 1 hora antes de tirar o dia e a hora. A Gold diária não faz essa soma, então o total do dia do ML aqui difere da fato pelos pedidos de 00:00 a 00:59 de Brasília.
- **TikTok:** `raw.tiktok_shop_orders`, deduplicada por pedido, com os status comerciais do conector.
- **Shopee:** `silver.stg_shopee_orders` mais os itens, sem cancelados, somando `item_total`. Entram só as lojas com API.
- **Kokeshi na Shopee:** fica fora, porque vem de arquivo sem a hora de cada pedido. O bloco avisa quando o filtro a inclui.

Conferido em 03/11/2026, de 26/10 a 02/11: a soma por dia bate com a fato publicada. Shopee e TikTok batem exato. No ML, a única diferença é a da hora de fuso.

## Implantação, nesta ordem

1. **Mesclar este PR** no `mktplace`.
2. **Airflow** (`goca-se/airflow`): fazer um PR que sobe o pin `MKTPLACE_PIPELINES_SHA` para o merge e acrescenta o passo `dia_vendas_hora` à DAG do dia corrente. Primeiro para a develop, depois o `-para-main`.
3. **Esperar a imagem nova no ar.** O commit "update image" precisa aparecer no `goca-se/managed-apps`.
4. **Só então aplicar a migration no Neon:** `alembic upgrade 039`. O PMA recusa rodar quando a cabeça do Neon não existe no grafo do pin, por isso a imagem vem antes. Até a migration, o passo novo falha e o bloco diz "indisponível". O resto da Gerencial não é afetado.
5. **Dar acesso ao perfil de escrita da Torre:**

   ```sql
   GRANT SELECT, INSERT, DELETE ON marts.fact_marketplace_hourly_sales TO tower_dst_rw;
   GRANT SELECT, INSERT, UPDATE ON marts.fact_marketplace_hourly_sales_fonte TO tower_dst_rw;
   ```

   O perfil já lê `marts.dim_loja`. Se a `DATABASE_URL` da API no Render não for a dona das tabelas, dê também `GRANT SELECT` nas duas a esse perfil.

## Rodar à mão

```bash
python -m pipelines.sync_vendas_por_hora            # dry-run: lê e mostra o resumo, não grava
python -m pipelines.sync_vendas_por_hora --apply    # grava no Neon
```
