# Expedição Shopee da Kokeshi pelo export do Seller Centre

A Kokeshi não tem app na Shopee Open Platform, então não aparece em `raw.shopee_orders` e a aba Expedição nunca a mostrou. A partir deste runbook, o operador da Kokeshi (o bot) carrega o export de pedidos do Seller Centre a cada 2 horas. A Kokeshi passa a aparecer na Expedição Shopee nos mesmos lugares das outras marcas:

- os cartões (vencidos, perto de vencer, mais de 48 h e travados);
- a tabela por marca;
- as listas de pedidos;
- a Jornada (fila por etapa);
- a tendência.

## Como funciona

```text
Seller Centre (export de pedidos)
  -> bot baixa o .xlsx
  -> python -m pipelines.expedicao.shopee_export_cli --apply    (máquina do bot)
       grava o último estado de cada pedido   marts.expedicao_shopee_export_pedido
       publica a fila e o resumo da conta     marts.expedicao_fila_export / _refresh_run_export
  -> API da Torre junta a Kokeshi com as outras quatro contas  -> tela
```

- **Nada passa pelo Data Mart nem pelo Airflow.** As tabelas ficam no Neon, onde o bot já grava a diária.
- O publicador agendado (`marketplace_tower_expedicao_refresh`) não é alterado. Ele apaga e regrava a fila da Shopee a cada 2 h, e por isso a Kokeshi tem tabelas próprias.
- A Kokeshi **não** entra em `marts.dim_seller_account`. Lá, o publicador agendado passaria a esperá-la no Data Mart e travaria a Shopee inteira. O cadastro dela fica em `marts.expedicao_conta_export`, com `shop_id` 395166866.
- A classificação é a mesma das outras contas (`transform.build_fila_shopee`): vencido, perto de vencer, mais de 48 h, lento contra a mediana de 30 dias e etapa.
- Status do export:

  | No export | Vira |
  |---|---|
  | "A Enviar" com rastreio | "Aguardando coleta" |
  | "A Enviar" sem rastreio | "A preparar" |

  O operador mediu essa regra em 881 pedidos de um dia inteiro, com 100% de acerto.
- "Tempo de Envio" é a hora da coleta (nos Full, a primeira chegada ao centro logístico). Pedido que já tem essa hora sai da fila.
- Nenhuma coluna do comprador é lida. O número de rastreamento só vira "tem ou não tem".

## Precondição (uma vez)

1. Rodar `alembic upgrade 034` no Neon, que cria as quatro tabelas e o cadastro da Kokeshi. Nenhuma tabela existente é alterada.
2. Dar acesso ao perfil de escrita da Torre, o mesmo da diária. O GRANT fica fora da migration, como na 032, porque o nome do perfil não é versionado:

   ```sql
   GRANT SELECT ON marts.expedicao_conta_export TO tower_dst_rw;
   GRANT SELECT, INSERT, UPDATE, DELETE ON marts.expedicao_shopee_export_pedido,
         marts.expedicao_fila_export, marts.expedicao_refresh_run_export TO tower_dst_rw;
   ```

   Esse perfil já tem leitura em `dim_loja`, `dim_seller_account` e na fila, e escrita em `audit.source_sync_run`.

## Rotina do bot (a cada 2 horas)

1. No Seller Centre, em **Meus Pedidos**, exportar **todos os pedidos**, filtrando por **data de criação**, dos **últimos 30 dias até hoje**.
   - O arquivo vem como `Order.all.order_creation_date.AAAAMMDD_AAAAMMDD.xlsx`.
   - Se vier em partes (`..._part_1_of_2.xlsx`), baixe todas.
2. Não renomeie e não abra no Excel. Aguarde o download terminar.
3. Rode a prévia, que lê e mostra os números sem gravar nada:

   ```bash
   python -m pipelines.expedicao.shopee_export_cli --conta kokeshi \
     --arquivo "/caminho/Order.all.order_creation_date.20261004_20261103.xlsx"
   ```

   Com partes, repita `--arquivo` para cada uma.
4. Confira a linha **"Fila da Expedicao"**. Ela deve bater com "A Enviar" no Seller Centre, menos os pedidos que já têm "Tempo de Envio".
5. Publique com o mesmo comando mais `--apply`.
6. Arquive os arquivos usados fora da pasta de entrada. Eles têm dados de comprador e não vão para o Git.

O comando usa o `DATABASE_URL` do Neon, o mesmo da diária. Ele lê do ambiente ou do `.env` da raiz do repositório.

**Se o export de 30 dias demorar demais:** entre duas cargas do export geral, pode-se usar o export da aba **A Enviar** (`Order.toship.order_creation_date...`). A fila fica igual. O export geral deve rodar pelo menos uma vez por dia, porque é dele que vem a mediana de lentidão.

## O comando recusa

| Saída | Motivo | O que fazer |
|---|---|---|
| 3 `EXPORT RECUSADO` | Export não termina hoje, download com mais de 3 h, parte faltando, coluna obrigatória ausente, data fora do formato, nome alterado | Baixar de novo, sem renomear, com todas as partes |
| 6 | Migration 034 ausente; conta não cadastrada, inativa ou que já vem da API | Avisar o time da Torre |
| 2 | Outra carga em andamento | Esperar e rodar de novo |
| 1, 4, 5 | Falha de banco, commit indeterminado, auditoria incompleta | Avisar o time; **não** repetir em laço |

Status fora do mapeamento (por exemplo, um status novo de devolução) **não** trava a carga. Ele aparece na linha "Status sem mapeamento", fica fora da fila e deve ser avisado ao time.

## Na tela

- A Kokeshi aparece na tabela com **"atualizada dd/mm HH:mm"** quando a carga dela é de outra hora que a "Última atualização" do topo.
- Se o bot parar, o frescor da Kokeshi passa a "Fonte atrasando" depois de 8 h e a "Fonte desatualizada" depois de 24 h, contados contra a fotografia mais recente da tela.
- Se a fila da Kokeshi publicada estiver incoerente (lote partido), ela sai da resposta e volta a "fora da cobertura". As outras quatro contas continuam no ar.

## Fora deste runbook

A **LDR operacional e a coorte** da Kokeshi ainda não são calculadas. A LDR está desligada para todas as marcas: a DAG está pausada no Airflow. Os dados de que ela precisa já ficam gravados em `marts.expedicao_shopee_export_pedido`: pagamento, prazo, coleta, status, FBS e motivo de cancelamento. Quando a LDR for ligada, o bot não muda nada.
