# Contrato: LDR no topo e seção Performance da Expedição

> CX-MKT-2/3, 03/11/2026. Pedido do usuário: "no topo, para cada marketplace, o LDR de cada marca, com meta e
> teto" e "a performance oficial dentro da Expedição, numa subseção Performance". Complementa
> `docs/expedicao_contrato.md` (regras de prazo) e `docs/performance_contas_contrato.md` (métricas oficiais).

## 1. O que entra na tela

Ordem da Expedição, nos três canais:

1. Cabeçalho, canal, filtros (como antes).
2. **LDR · atraso de envio, últimos 7 dias** (novo, em destaque): matriz marca × marketplace. Cada célula mostra a taxa,
   uma barra com as marcas de **meta** e **teto** do canal, o selo "Dentro da meta / Entre meta e teto / Acima do
   teto" e "N atrasados de M com prazo vencido". Segue o filtro de marca. É **cálculo Torre**.
3. Seções existentes (Risco atual, Prazo, Jornada, Evolução…).
4. **Performance · nota oficial de cada conta** (novo, só na visão do gestor): a nota que a plataforma usa para
   reputação e penalidade, como o painel do vendedor mostra. É **oficial**.

As duas partes ficam atrás de `NEXT_PUBLIC_EXPEDICAO_PERFORMANCE_ENABLED` (web) e a Performance também atrás de
`EXPEDICAO_PERFORMANCE_API_ENABLED` (API). Ambas nascem desligadas.

## 2. LDR: regra de cada canal

LDR = (pedidos atrasados + pedidos com prazo vencido ainda não coletados) ÷ pedidos com prazo vencendo na janela.
Janela: os últimos 7 dias. Ausência de base é "—", nunca 0%.

| Canal | Prazo | Marco de "enviado" | Fonte | Situação em 03/11/2026 |
|---|---|---|---|---|
| Shopee | `ship_by_date` da Shopee (dias úteis já aplicados) | coleta (`pickup_done_time`) | `GET /expedicao/shopee/ldr?days=7`, LDR operacional (EXPEDICAO-SHOPEE-LDR-1; FBS fora; cancelado sem coleta fora) | Pronto, atrás de flag: falta a migration 030 no Neon, as flags e despausar a publicação |
| TikTok | 2 dias úteis após o pagamento (prazo reconstruído; o oficial `tts_sla_time` substitui quando ingerido) | coleta | `GET /expedicao/tiktok?event=coleta&days=7` | No ar |
| Mercado Livre | SLA oficial de despacho (`/shipments/{id}/sla` → `expected_date`; no Mercado Coleta é o horário AGENDADO da coleta) | coleta até o **fim do dia** do prazo (`picked_up`/`dropped_off`/`in_hub` em `substatus_history`), exceto quando o ML marca `delayed` | `GET /expedicao/ml/ldr?days=7` (lê `raw.ml_shipments` no Data Mart) | Série desde 03/11/2026; com menos de 7 dias fechados a célula diz "janela parcial: N de 7 dias" |

**Regra do ML (validada no Data Mart em 03/11/2026).** Comparar a coleta com o horário exato do SLA daria ~80% de
"atraso" que o próprio ML não marca: no Mercado Coleta o SLA é o horário agendado e a coleta é registrada minutos ou
horas depois (mediana +0,5 h). A regra que bate com o `sla_status = 'delayed'` do ML:

- base: envios de modalidade com coleta (`cross_docking`, `xd_drop_off`, `drop_off`) com SLA vencendo no dia
  (Brasília), menos cancelados sem coleta;
- atrasado: coletado depois do fim do dia do prazo, OU `delayed` pelo ML, OU sem coleta com o dia fechado;
- fora da base (contado): enviado/entregue sem marco de coleta.

Conferência: "coletado depois do dia" = `delayed` do ML em 31/10 (Kokeshi, 15 de 15) e 30/10 (Rituária, 3 de 3).

O `delayed` do ML vale mesmo com coleta no mesmo dia: em 04/11/2026 a Rituária entregou 78 envios na agência às
16:21 com SLA às 14:30/15:00, e o ML marcou todos como `delayed` (44% na janela de 03–04/11). A regra da Torre já
os conta como atrasados; o texto da tela (ⓘ e subtítulo da coluna) passou a dizer isso (05/11/2026).
Prévia de 03/11 (dia ainda aberto): Barbour's 1/99, Kokeshi 0/340, Lescent 0/51, Rituária 3/82. A série começa em
03/11 porque antes disso só envios presos tinham SLA gravado (taxa enviesada).

O número oficial de atraso do ML existe à parte: `delayed_handling_time` da reputação (60 dias), mostrado na seção
Performance como "Envios com atraso".

## 3. Meta e teto

Valores em `apps/web/src/lib/expedicao-ldr-topo.ts` (`META_TETO`), **confirmados pelo negócio em 04/11/2026** (junto com as regras de LDR do ML e do TikTok desta seção):

| Canal | Meta | Teto | Origem do teto |
|---|---|---|---|
| Shopee | 2% | 5% | referência interna |
| Mercado Livre | 3% | 6% | limite MercadoLíder para envios com atraso (doc de reputação do ML) |
| TikTok | 2% | 4% | referência de 4% que a Torre já usava, sem fonte oficial publicada |

Limite exato conta como dentro (`taxa <= meta` → dentro; `taxa <= teto` → entre; acima disso → acima do teto).

## 4. Performance: fontes oficiais

Rota `GET /api/v1/expedicao/performance?canal=mercadolivre|shopee|tiktokshop&brands=`. Lê a fotografia diária mais
recente de cada conta no Data Mart. Tabela ainda não criada = `sem_fonte` ("Aguardando a primeira captura"), checado
com `to_regclass`, nunca erro nem zero.

| Canal | Tabela (goca-airflow) | O que a tela mostra | Meta |
|---|---|---|---|
| ML | `raw.ml_seller_reputation_snapshot` (DAG `mercadolivre_seller_reputation_snapshot`, 07:10 BRT, migration 55) | MercadoLíder, termômetro (`level_id`), vendas em 60 dias, Reclamações, Mediações (se a API devolver), Canceladas por você, Envios com atraso; quantidade de vendas afetadas | limites MercadoLíder MLB: 1% / 0,5% / 0,5% / 6% |
| Shopee | `raw.shopee_account_health_snapshot` + `_metric` (DAG `shopee_account_health_snapshot`, 07:40 BRT, migration 54) | nota geral (Ruim → Excelente), métricas de primeiro nível com valor atual, pontos de penalidade e punições | o `target` que a Shopee devolve por loja |
| TikTok | — | `sem_fonte`: o SPS tem API só para lojas dos EUA pela doc; o probe `tiktok_cx_capability_probe` testa as lojas BR | — |

## 5. Para ligar

1. goca-airflow (branch `feat/cx-marketplaces-probes`): aplicar migrations 54 e 55, rodar as DAGs uma vez em
   `dry_run`, conferir, ligar `ML_REPUTATION_WRITE_ENABLED` e `SHOPEE_ACCOUNT_HEALTH_WRITE_ENABLED` e despausar.
2. Grant de `SELECT` ao usuário do Data Mart da API em `raw.ml_seller_reputation_snapshot`,
   `raw.shopee_account_health_snapshot` e `raw.shopee_account_health_metric`.
3. Render: `EXPEDICAO_PERFORMANCE_API_ENABLED=true`. Vercel: `NEXT_PUBLIC_EXPEDICAO_PERFORMANCE_ENABLED=true`.
4. Para a coluna Shopee do LDR: a migration 030 e as flags da LDR Shopee (`docs/expedicao_contrato.md`).
