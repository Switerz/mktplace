# Kokeshi nas abas CX e Performance: o que o bot coleta e como grava no banco

A Kokeshi não tem app na Shopee Open Platform. As outras quatro lojas (Ápice, Barbour's, Lescent e Rituária) chegam pela API, via Airflow. A Kokeshi chega pelo bot: ele lê as telas do Seller Centre e **grava direto no banco da Torre (Neon)**, com o mesmo acesso que já usa para os pedidos da Expedição.

Com isso, a Kokeshi passa a aparecer em:

- **Performance → Shopee**: nota da loja, métricas da Saúde da Conta com a meta da Shopee, pontos de penalidade e punições.
- **CX → Atendimento**: % respondido, nota, conversas não lidas, conversas no período, aguardando resposta e contact rate.
- **CX → Pareto dos problemas**: cancelamentos com motivo, devoluções com motivo e o valor reembolsado.

## O que não muda

**Pedidos e cancelamentos** saem dos pedidos que o bot já grava para a Expedição (`marts.expedicao_shopee_export_pedido`), com o motivo de cancelamento. **Não há nada novo a coletar** para isso. O export **geral** (todos os status, últimos 30 dias) precisa entrar pelo menos 1x por dia, porque o da aba "A Enviar" não traz cancelados.

## O que o bot passa a coletar

São três blocos independentes, cada um com o seu SQL. O bot pode gravar só o bloco que leu naquele momento.

| Bloco | Onde, no Seller Centre | Frequência | Tabela no banco |
|---|---|---|---|
| Saúde da Conta | **Minha Conta → Saúde da Conta** (`seller.shopee.com.br/portal/accounthealth`) | 1x por dia, depois das 08:00 | `marts.shopee_saude_metrica_export` e `marts.shopee_saude_conta_export` |
| Chat | Contador de **não lidas** no chat do vendedor e **Dados de Negócio → Chat** ("Últimos 7 dias" e "Últimos 30 dias") | não lidas a cada 2 h; painel 1x por dia | `marts.shopee_chat_export` |
| Devoluções | **Pedidos → Devolução/Reembolso**, solicitações criadas nos últimos 30 dias, em todas as abas de status | 1x por dia | `marts.shopee_returns_export` |

**Regras para os três blocos:**

- Tudo é da conta `'kokeshi'`, que já vem escrita no SQL.
- Datas e horas vão no horário de Brasília, no formato `AAAA-MM-DD HH:MM` (ex.: `2026-11-05 08:30`). O SQL converte.
- Números vão com **ponto decimal**, sem `%` nem `R$` (ex.: `6.2`, `59.90`).
- **O que não aparecer na tela vai como `NULL`, nunca como 0.** Zero só quando a tela mostra zero.
- Rodar o mesmo SQL de novo não duplica nada: ele atualiza.
- **Não grave** nome, endereço, telefone, CPF, fotos nem texto livre do comprador.

## 1. Saúde da Conta

O bot troca só:

- a data `2026-11-05` (3 vezes) e a hora da leitura `2026-11-05 08:30`;
- as linhas `(codigo, valor, anterior)`, uma por métrica que a tela mostra;
- o nível da loja (`'Bom'`): `Excelente`, `Bom`, `Precisa melhorar` ou `Ruim`;
- os pontos de penalidade e as punições em andamento (`0` e `0` no exemplo).

`valor` é o período atual. `anterior` é o período anterior, quando a tela mostra; senão, `NULL`. Métrica que não aparece na tela fica fora da lista. As metas já estão no SQL: são as mesmas que a Shopee devolve para as outras lojas. A contagem de métricas fora da meta é calculada pelo próprio SQL.

| Código | Nome na tela (pode variar um pouco) | Unidade | Meta |
|---|---|---|---|
| `taxa_envio_atrasado` | Taxa de envio atrasado | % | < 5 |
| `taxa_nao_cumprimento` | Taxa de pedidos não concluídos | % | < 8 |
| `taxa_cancelamento` | Taxa de cancelamento | % | < 8 |
| `taxa_devolucao_reembolso` | Taxa de devolução/reembolso | % | < 8 |
| `envios_aos_sabados` | Envios aos sábados | % | ≥ 20 |
| `tempo_medio_preparacao_dias` | Tempo médio de preparação | dias | < 1,3 |
| `taxa_entrega_no_prazo` | Taxa de entrega no prazo | % | ≥ 75 |
| `anuncios_pre_venda_pct` | % de anúncios em pré-venda | % | ≤ 20 |
| `violacoes_graves` | Violações graves de anúncio | número | ≤ 0 |
| `outras_violacoes` | Outras violações de anúncio | número | ≤ 0 |
| `taxa_resposta_chat` | Taxa de resposta do chat | % | ≥ 60 |
| `nota_loja` | Avaliação da loja | nota 0–5 | ≥ 4,5 |

```sql
BEGIN;

-- 1) Metricas do dia: apaga as do dia e grava de novo
DELETE FROM marts.shopee_saude_metrica_export
 WHERE brand = 'kokeshi' AND captured_date = DATE '2026-11-05';

INSERT INTO marts.shopee_saude_metrica_export
       (brand, captured_date, metric_id, metric_name, metric_type, parent_metric_id, unit,
        current_period, last_period, target_value, target_comparator)
SELECT 'kokeshi', DATE '2026-11-05', c.metric_id, c.metric_name, c.metric_type, c.parent_metric_id, c.unit,
       e.valor::numeric, e.anterior::numeric, c.meta, c.comparador
  FROM (VALUES
        -- (codigo, valor, anterior)  <- o bot preenche so' estas linhas
        ('taxa_envio_atrasado',         1.2,  0.9),
        ('taxa_nao_cumprimento',        0.8,  NULL),
        ('taxa_cancelamento',           0.5,  NULL),
        ('taxa_devolucao_reembolso',    0.3,  NULL),
        ('tempo_medio_preparacao_dias', 0.7,  NULL),
        ('taxa_resposta_chat',          92,   NULL),
        ('nota_loja',                   4.9,  NULL),
        ('violacoes_graves',            0,    NULL),
        ('outras_violacoes',            0,    NULL)
       ) AS e(codigo, valor, anterior)
  JOIN (VALUES
        ('taxa_envio_atrasado',            1, 'late_shipment_rate',        1, 0, 2,   5.0, '<'),
        ('taxa_nao_cumprimento',           3, 'non_fulfillment_rate',      1, 0, 2,   8.0, '<'),
        ('taxa_cancelamento',             42, 'cancellation_rate',         1, 3, 2,   8.0, '<'),
        ('taxa_devolucao_reembolso',      43, 'return_refund_rate',        1, 3, 2,   8.0, '<'),
        ('envios_aos_sabados',          2032, 'saturday_shipment_rate',    1, 0, 2,  20.0, '>='),
        ('tempo_medio_preparacao_dias', 2033, 'avg_preparation_time_ps',   1, 0, 4,   1.3, '<'),
        ('taxa_entrega_no_prazo',       2037, 'otdr_dd_rate',              1, 0, 2,  75.0, '>='),
        ('anuncios_pre_venda_pct',        12, 'pre_order_listing_rate',    2, 0, 2,  20.0, '<='),
        ('violacoes_graves',              52, 'severe_listing_violations', 2, 0, 1,   0.0, '<='),
        ('outras_violacoes',              53, 'other_listing_violations',  2, 0, 1,   0.0, '<='),
        ('taxa_resposta_chat',            11, 'response_rate',             3, 0, 2,  60.0, '>='),
        ('nota_loja',                     22, 'shop_rating',               3, 0, 1,   4.5, '>=')
       ) AS c(codigo, metric_id, metric_name, metric_type, parent_metric_id, unit, meta, comparador)
    ON c.codigo = e.codigo
 WHERE e.valor IS NOT NULL;

-- 2) Resumo da conta (a contagem de metricas fora da meta sai das linhas acima)
INSERT INTO marts.shopee_saude_conta_export
       (brand, captured_date, captured_at, overall_rating, fulfillment_failed, listing_failed,
        custom_service_failed, penalty_points, ongoing_punishments, source_sha256)
SELECT 'kokeshi', DATE '2026-11-05',
       TIMESTAMP '2026-11-05 08:30' AT TIME ZONE 'America/Sao_Paulo',
       CASE lower(trim('Bom'))
            WHEN 'excelente' THEN 4 WHEN 'bom' THEN 3
            WHEN 'precisa melhorar' THEN 2 WHEN 'ruim' THEN 1 END,
       CASE WHEN count(*) > 0 THEN count(*) FILTER (WHERE m.metric_type = 1 AND m.fora) END,
       CASE WHEN count(*) > 0 THEN count(*) FILTER (WHERE m.metric_type = 2 AND m.fora) END,
       CASE WHEN count(*) > 0 THEN count(*) FILTER (WHERE m.metric_type = 3 AND m.fora) END,
       0,      -- pontos de penalidade
       0,      -- punicoes em andamento
       encode(sha256(convert_to('kokeshi|saude|' || clock_timestamp()::text, 'UTF8')), 'hex')
  FROM (SELECT metric_type,
               CASE target_comparator
                    WHEN '<'  THEN NOT (current_period <  target_value)
                    WHEN '<=' THEN NOT (current_period <= target_value)
                    WHEN '>=' THEN NOT (current_period >= target_value)
                    WHEN '>'  THEN NOT (current_period >  target_value) END AS fora
          FROM marts.shopee_saude_metrica_export
         WHERE brand = 'kokeshi' AND captured_date = DATE '2026-11-05'
           AND parent_metric_id = 0) AS m
ON CONFLICT (brand, captured_date) DO UPDATE SET
       captured_at = EXCLUDED.captured_at, overall_rating = EXCLUDED.overall_rating,
       fulfillment_failed = EXCLUDED.fulfillment_failed, listing_failed = EXCLUDED.listing_failed,
       custom_service_failed = EXCLUDED.custom_service_failed,
       penalty_points = EXCLUDED.penalty_points, ongoing_punishments = EXCLUDED.ongoing_punishments,
       source_sha256 = EXCLUDED.source_sha256, loaded_at = now();

COMMIT;
```

## 2. Chat

Uma linha por leitura. O bot troca a hora da leitura e os números; o que não leu vai como `NULL`. Na leitura de 2 em 2 horas, normalmente só há as não lidas e o "aguardando", e o resto vai `NULL`.

| Campo | O que ler |
|---|---|
| não lidas | Contador de conversas não lidas no chat do vendedor |
| aguardando resposta | Conversas em que a última mensagem é do comprador (filtro "Não respondidas", se houver) |
| conversas 7 d / 30 d | Painel de Chat: número de conversas/chats recebidos em cada período |
| período | Período, em dias, dos campos de baixo (normalmente 30) |
| visitantes | Visitantes/compradores que abriram o chat |
| taxa de resposta | Taxa de resposta do chat (%) |
| tempo médio de resposta | Em **minutos**: troque só o `35`. O SQL converte para segundos. Sem o dado, troque a linha inteira por `NULL,` |
| CSAT | Satisfação do chat (%) |
| conversão | Taxa de conversão do chat (%) |

```sql
INSERT INTO marts.shopee_chat_export
       (brand, captured_at, unread_count, aguardando_resposta, conversas_7d, conversas_30d,
        periodo_dias, visitantes_chat, taxa_resposta_pct, tempo_resposta_medio_s, csat_pct,
        conversao_chat_pct, source_sha256)
VALUES ('kokeshi',
        TIMESTAMP '2026-11-05 08:30' AT TIME ZONE 'America/Sao_Paulo',  -- hora da leitura (Brasilia)
        6,      -- nao lidas
        4,      -- aguardando resposta
        310,    -- conversas nos ultimos 7 dias
        1250,   -- conversas nos ultimos 30 dias
        30,     -- periodo (dias) dos campos abaixo
        4800,   -- visitantes do chat
        92,     -- taxa de resposta (%)
        round(35 * 60)::int,  -- tempo medio de resposta: troque 35 pelos MINUTOS
        90.5,   -- CSAT (%)
        12.1,   -- conversao do chat (%)
        encode(sha256(convert_to('kokeshi|chat|' || clock_timestamp()::text, 'UTF8')), 'hex'))
ON CONFLICT (brand, captured_at) DO UPDATE SET
       unread_count = EXCLUDED.unread_count, aguardando_resposta = EXCLUDED.aguardando_resposta,
       conversas_7d = EXCLUDED.conversas_7d, conversas_30d = EXCLUDED.conversas_30d,
       periodo_dias = EXCLUDED.periodo_dias, visitantes_chat = EXCLUDED.visitantes_chat,
       taxa_resposta_pct = EXCLUDED.taxa_resposta_pct,
       tempo_resposta_medio_s = EXCLUDED.tempo_resposta_medio_s, csat_pct = EXCLUDED.csat_pct,
       conversao_chat_pct = EXCLUDED.conversao_chat_pct, source_sha256 = EXCLUDED.source_sha256,
       loaded_at = now();
```

## 3. Devoluções

Uma linha por solicitação, com **todas** as criadas nos últimos 30 dias, inclusive as canceladas. O status muda com o tempo, e o banco guarda o último status lido.

O bot escreve **o status e o motivo como aparecem na tela** (ex.: "Reembolso concluído", "Não recebi o pedido"), e o próprio SQL traduz para os códigos da Shopee. Motivo que o SQL não conhece entra com o texto original e aparece na tela assim mesmo. O valor reembolsado vai como número (`59.90`) ou `NULL`.

Lista grande pode ir em blocos de até 500 linhas: é o mesmo SQL, repetido com outras linhas.

```sql
INSERT INTO marts.shopee_returns_export AS atual
       (brand, return_sn, order_sn, create_time, update_time, status, status_export,
        reason, reason_export, refund_amount, currency, captured_at)
SELECT DISTINCT ON (d.id)
       'kokeshi', left(trim(d.id), 64), nullif(left(trim(d.pedido), 64), ''),
       d.criada_em::timestamp AT TIME ZONE 'America/Sao_Paulo',
       d.atualizada_em::timestamp AT TIME ZONE 'America/Sao_Paulo',
       -- status em portugues -> codigo da Shopee (o mesmo da API)
       CASE
         WHEN upper(trim(d.status)) IN ('ACCEPTED','CANCELLED','PROCESSING','REQUESTED','JUDGING','SELLER_DISPUTE','CLOSED')
              THEN upper(trim(d.status))
         WHEN s.t LIKE '%cancel%'                                        THEN 'CANCELLED'
         WHEN s.t ~ '(disputa|analise|revisao)'                          THEN 'JUDGING'
         WHEN s.t ~ '(conclu|reembolsad|reembolso realizado|aprovad|aceit)' THEN 'ACCEPTED'
         WHEN s.t ~ '(fechad|encerrad)'                                  THEN 'CLOSED'
         WHEN s.t ~ '(solicitad|pendente)'                               THEN 'REQUESTED'
         WHEN s.t ~ '(aguardando|processando|em andamento|em transito)'  THEN 'PROCESSING'
       END,
       left(trim(d.status), 80),
       -- motivo em portugues -> codigo da Shopee; sem traducao fica o proprio texto
       CASE
         WHEN nullif(trim(d.motivo), '') IS NULL OR trim(d.motivo) = '-'  THEN NULL
         WHEN r.t ~ '(diferente da descricao|diferente do anuncio|nao corresponde)' THEN 'DIFFERENT_DESCRIPTION'
         WHEN r.t ~ '(nao receb|nao cheg)'                         THEN 'NOT_RECEIPT'
         WHEN r.t ~ '(vazad|vazou|derram)'                         THEN 'SPILLED_CONTENTS'
         WHEN r.t ~ '(vencid|validade)'                            THEN 'EXPIRED_PRODUCT'
         WHEN r.t ~ '(falsific|falso)'                             THEN 'ITEM_FAKE'
         WHEN r.t ~ '(defeito|nao funciona)'                       THEN 'FUNCTIONAL_DMG'
         WHEN r.t ~ '(danific|quebrad|avariad)'                    THEN 'DAMAGED_OTHERS'
         WHEN r.t ~ '(faltando|faltante|incomplet)'                THEN 'ITEM_MISSING'
         WHEN r.t ~ 'errad'                                        THEN 'WRONG_ITEM'
         WHEN r.t ~ 'usado'                                        THEN 'USED_PRODUCT'
         WHEN r.t ~ '(mudei de ideia|mudou de ideia|nao quero mais|desist|arrepend)' THEN 'CHANGE_MIND'
         WHEN r.t ~ 'acordo'                                       THEN 'MUTUAL_AGREE'
         ELSE left(trim(d.motivo), 60)
       END,
       nullif(left(trim(d.motivo), 200), ''),
       round(d.valor::numeric, 2), 'BRL',
       TIMESTAMP '2026-11-05 08:30' AT TIME ZONE 'America/Sao_Paulo'  -- hora da leitura (Brasilia)
  FROM (VALUES
        -- (id da solicitacao, pedido, criada em, atualizada em, status na tela, motivo na tela, valor reembolsado)
        ('2511010ABCDEF', '251031XYZ', '2026-11-01 10:22', NULL, 'Reembolso concluído', 'Não recebi o pedido', 59.90),
        ('2511020GHIJKL', '251101QWE', '2026-11-02 09:05', NULL, 'Cancelado', 'Mudei de ideia', NULL)
       ) AS d(id, pedido, criada_em, atualizada_em, status, motivo, valor)
 CROSS JOIN LATERAL (SELECT lower(translate(coalesce(d.status, ''), 'áàâãéêíóôõúüçÁÀÂÃÉÊÍÓÔÕÚÜÇ', 'aaaaeeiooouucAAAAEEIOOOUUC')) AS t) AS s
 CROSS JOIN LATERAL (SELECT lower(translate(coalesce(d.motivo, ''), 'áàâãéêíóôõúüçÁÀÂÃÉÊÍÓÔÕÚÜÇ', 'aaaaeeiooouucAAAAEEIOOOUUC')) AS t) AS r
 ORDER BY d.id
ON CONFLICT (brand, return_sn) DO UPDATE SET
       order_sn = EXCLUDED.order_sn, create_time = EXCLUDED.create_time,
       update_time = EXCLUDED.update_time, status = EXCLUDED.status,
       status_export = EXCLUDED.status_export, reason = EXCLUDED.reason,
       reason_export = EXCLUDED.reason_export, refund_amount = EXCLUDED.refund_amount,
       currency = EXCLUDED.currency, captured_at = EXCLUDED.captured_at, loaded_at = now()
 WHERE EXCLUDED.captured_at >= atual.captured_at;
```

## Como conferir

Esta consulta mostra a última carga de cada bloco:

```sql
SELECT 'saude' AS bloco, max(captured_at) AS ultima, count(*) AS linhas FROM marts.shopee_saude_conta_export WHERE brand = 'kokeshi'
UNION ALL SELECT 'metricas', max(loaded_at), count(*) FROM marts.shopee_saude_metrica_export WHERE brand = 'kokeshi'
UNION ALL SELECT 'chat', max(captured_at), count(*) FROM marts.shopee_chat_export WHERE brand = 'kokeshi'
UNION ALL SELECT 'devolucoes', max(captured_at), count(*) FROM marts.shopee_returns_export WHERE brand = 'kokeshi';
```

Status e motivos que o SQL não traduziu (avise o time da Torre para incluir):

```sql
SELECT status_export, reason_export, status, reason, count(*)
  FROM marts.shopee_returns_export
 WHERE brand = 'kokeshi' AND (status IS NULL OR reason !~ '^[A-Z_]+$')
 GROUP BY 1, 2, 3, 4;
```

## Se der erro

| Erro do banco | Causa | O que fazer |
|---|---|---|
| `relation "marts.shopee_..." does not exist` | Migration 036 não aplicada | Avisar o time da Torre |
| `permission denied` | O perfil do bot não tem acesso às 4 tabelas | Avisar o time (GRANT abaixo) |
| `invalid input syntax for type numeric` ou `timestamp` | Número com vírgula, `%` ou `R$`; ou data fora de `AAAA-MM-DD HH:MM` | Corrigir o valor e rodar de novo |
| `violates foreign key constraint` | Conta diferente de `'kokeshi'` | Não alterar a conta no SQL |

No bloco da Saúde da Conta, um erro desfaz tudo (`BEGIN`/`COMMIT`). Rode `ROLLBACK;` e depois rode de novo, já corrigido.

## Na tela

- A Kokeshi aparece na Performance → Shopee com o aviso "Capturada pelo bot no Seller Centre".
- Enquanto o bot não grava um bloco, a Torre mostra "aguardando a primeira captura do bot", nunca zero.
- Se a Saúde da Conta ficar mais de 2 dias sem atualização, a conta ganha o aviso "a captura diária está atrasada".

## Para o time da Torre

1. A migration 036 cria as 4 tabelas e dá acesso ao perfil `tower_dst_rw`, se ele existir. Se o bot usar outro perfil:

   ```sql
   GRANT SELECT, INSERT, UPDATE, DELETE ON marts.shopee_saude_conta_export,
         marts.shopee_saude_metrica_export, marts.shopee_chat_export,
         marts.shopee_returns_export TO <perfil_do_bot>;
   ```
2. As tabelas têm a mesma forma das `raw.*` do Data Mart (Airflow), e a API lê as duas fontes com a mesma regra.
3. A tradução de status e motivo do SQL acima é a mesma de `pipelines/cx/kokeshi.py` (testada em 22 textos, sem diferença). Quem tiver Python pode usar `python -m pipelines.cx.kokeshi_cli`, que valida um JSON e grava as mesmas linhas. Ao mudar um mapa, mude os dois.
