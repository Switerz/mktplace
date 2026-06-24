# Auditoria - Aba Qualidade

Criado: 2026-06-24
Referencia principal: Mai/2026
Fonte operacional: Neon `marts.fact_marketplace_daily_performance` + `marts.dim_loja`
Endpoint: `GET /api/v1/performance/quality?marketplace=&ref_month=`

---

## 1. Objetivo da aba

A aba Qualidade deve responder, por marketplace e marca:

- quanto esta sendo cancelado;
- quanto esta ficando nao entregue ou pendente;
- qual e o tempo medio de entrega;
- onde Shopee tem devolucao relevante;
- quais metricas sao confiaveis vs. proxies.

Essa aba e critica porque mistura operacao/logistica com qualidade de pedido. Um denominador errado muda a leitura de prioridade operacional.

---

## 2. Contrato atual

| Camada | Arquivo |
|---|---|
| Frontend | `apps/web/app/qualidade/page.tsx` |
| API client | `apps/web/src/lib/api-client.ts` (`fetchQuality`) |
| Router | `apps/api/app/routers/performance.py` -> `perf_svc.get_quality()` |
| Service | `apps/api/app/services/performance_service.py` -> `get_quality()` |
| Schema | `apps/api/app/schemas/performance.py` (`QualityKpis`, `QualityBrandRow`) |

Filtros: `all`, `tiktok`, `ml`, `shopee` x `ref_month`.

---

## 3. Validacao Neon - Mai/2026

### 3.1 Cobertura e grao

Grao validado: `date + loja_id + marketplace_id`.

| Marketplace | Linhas | Dias | Lojas | Orders | Canceled | Returned | Delivered | Duplicatas |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| TikTok | 155 | 31 | 5 | 253.933 | 0 | 0 | 223.340 | 0 |
| ML | 93 | 31 | 3 | 45.432 | 2.062 | NULL | 38.973 | 0 |
| Shopee | 155 | 31 | 5 | 91.157 | 14.639 | 987 | 91.057 | 0 |

### 3.2 KPIs retornados pelo endpoint

| KPI | Valor Mai/26 | Status | Observacao |
|---|---:|---|---|
| `tiktok_avg_delivery_days` | 5,3d | Confiavel com ressalva | Calculado de `avg_delivery_hours`, cobertura 155/155 linhas em maio |
| `tiktok_cancel_rate` | NULL | Correto | `canceled_orders=0` em todos os meses; campo nao e confiavel para TikTok |
| `tiktok_problem_rate` | NULL | Correto por prudencia | `problem_rate=0` em todas as linhas; nao exibir falso 0% |
| `ml_cancel_rate_pct` | 4,34% | Confiavel | Denominador: `canceled / (orders + canceled)` |
| `ml_not_delivered_rate_pct` | 14,22% | Proxy | Calculado como `(orders - delivered_orders) / orders`; nao e a mesma metrica de shipment do gold |
| `ml_avg_delivery_days` | 4,7d | Confiavel | Media ponderada por entregues no SQL de marca; KPI reponderado por volume de pedidos, diferenca baixa em maio |
| `shopee_cancel_rate_pct` | 13,84% | Confiavel | Denominador: `canceled / (orders + canceled)` |
| `shopee_return_rate_pct` | 1,08% | Confiavel | Denominador: `returned / orders` |

---

## 4. Regras de calculo

### Cancelamento

Padrao usado no projeto:

```sql
canceled_orders / NULLIF(orders + canceled_orders, 0) * 100
```

Interpretacao: `orders` representa pedidos pagos/validos; `orders + canceled_orders` representa tentativas totais. Isso evita inflar a taxa quando os cancelados ja estao fora de `orders`.

### Nao entregue ML

Calculo atual:

```sql
(orders - delivered_orders) / NULLIF(orders, 0) * 100
```

Esse numero deve ser tratado como proxy de pendencia/nao-entrega, nao como taxa logistica final. Ele pode misturar pedidos em transito, atraso e nao entrega real, principalmente em meses recentes.

### Tempo medio de entrega

- TikTok usa `avg_delivery_hours / 24`, ponderado por `orders`.
- ML usa `avg_delivery_days`, ponderado por `delivered_orders` na agregacao por marca.
- Shopee nao tem tempo medio de entrega populado no mart atual.

---

## 5. Achados criticos e correcoes

### 5.1 Linhas Shopee vazias em meses parciais

Em Jun/26, o mart possui linhas Shopee ate 20/06, mas `orders`, `canceled_orders`, `returned_orders` e `delivered_orders` estao NULL. Como o SQL usava `COALESCE(SUM(...), 0)`, o backend podia devolver linhas de marca sem nenhuma metrica real.

Correcao aplicada em `get_quality`: linhas sem qualquer sinal de qualidade sao ignoradas antes de montar `brand_rows`.

### 5.2 UI nao deve vender proxy como metrica final

A tela agora sinaliza:

- `Nao Entregue ML` = proxy `pagos - entregues`;
- cancelamento/problema TikTok indisponiveis;
- compradores ML na secao de fidelizacao = soma diaria em validacao.

---

## 6. Status por canal

| Canal | Status | Confiavel agora | Limitacoes |
|---|---|---|---|
| TikTok | Parcial | Tempo medio de entrega | Cancelamento, problema, devolucao e retorno nao devem ser exibidos como 0; delivered historico so passa a fazer sentido a partir de abr/26 |
| ML | Parcial | Cancelamento e entrega media | Nao-entrega e proxy; compradores/recompra continuam soma diaria vs. deduplicado mensal |
| Shopee | Bom para maio fechado | Cancelamento e devolucao | Mes atual pode ter linhas vazias/null; sem tempo medio de entrega no mart |

---

## 7. Proximas melhorias de dados

1. Trazer do Data Mart/gold a metrica canonica de nao-entrega ML por shipment, se existir.
2. Adicionar campo de status/cobertura por marketplace e mes para evitar comparar meses parciais com meses fechados.
3. Separar `paid_orders`, `canceled_orders`, `delivered_orders` e `attempted_orders` no contrato para acabar com ambiguidade de denominador.
4. Criar metricas TikTok de qualidade so quando houver fonte real para cancelamento, reembolso, devolucao e problema.
5. Adicionar tempo medio Shopee quando houver fonte confiavel.

---

## 8. Arquivos alterados

| Arquivo | Mudanca |
|---|---|
| `apps/api/app/services/performance_service.py` | `get_quality` ignora linhas de marketplace sem nenhuma medida real, evitando linhas vazias em meses parciais |
| `apps/web/app/qualidade/page.tsx` | UI deixa claro que ML nao-entregue e proxy, ajusta empty-state por filtro e sinaliza limitacoes de TikTok/ML |
| `docs/sections/qualidade_audit.md` | Documento de auditoria da aba Qualidade |
