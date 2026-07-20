# Análise de Venda e Custos por UF — Auditoria e Design (Gate 4)

Status: **`gold.marketplace_region_daily` APLICADA no Data Mart (Gate 6A, autorização explícita do Mário)**. DDL e primeira carga executadas em transações separadas, com preflight de escrita aprovado imediatamente antes de cada uma. Sync Data Mart → Neon, endpoints da API e qualquer mudança de frontend **continuam não iniciados** (Gate 6B, gate separado).

**Gates desta frente (não confundir "desenhar" com "aplicar")**:
- **Gate 4** (auditoria read-only) — **concluído**: Shopee confirmado confiável, dedup aprovado; ML utilizável com causa raiz de cobertura confirmada; TikTok confirmado sem UF estrutural.
- **Gate 5** (design técnico + contrato de API + decisões de produto documentadas) — **concluído**: schema com numerador/denominador, contrato de endpoint com `coverage_warning`/`coverage_level`, decisão de barbours tomada (Opção A), fonte ML resolvida (`raw.ml_shipments`/`raw.ml_shipment_costs`), timezone confirmada (BRT nativo, sem conversão).
- **Gate 6A** (aplicação real — DDL + primeira carga no Data Mart) — **CONCLUÍDO**. Ver §10 para o resultado real (contagens, reconciliação, cobertura por marca/mês).
- **Gate 6B** (sync Data Mart → Neon, endpoints `/regioes/*`, qualquer mudança de frontend/deploy) — **não iniciado, gate separado**, requer autorização explícita adicional.

## 0. Execução — o que foi feito em cada sessão

**Sessão 1 (2026-07-07)**: não foi possível conectar ao Data Mart (`datamart_engine` não inicializava via `pydantic-settings`/`Settings()`, provavelmente por caractere especial na senha quebrando o parse padrão de `.env`). Durante a checagem, um comando de shell vazou por engano um fragmento da senha no transcript — reportado ao usuário, rotação recomendada.

**Sessão 2 (2026-07-08)**: a auditoria foi executada com sucesso, usando `dotenv_values(".env")` (carrega a variável como string bruta, sem o parsing adicional que travava no `pydantic-settings`) + SQLAlchemy, a partir de `apps/api/.venv`. Sessão aberta em modo **somente leitura** (`SET default_transaction_read_only = on` + `execution_options(postgresql_readonly=True)`). Confirmado antes de qualquer query:

```
SELECT 1                    -> 1
SELECT pg_is_in_recovery()  -> true   (replica de leitura, como já documentado)
```

Nenhuma URL, credencial ou dado com PII (nome, telefone, CPF, endereço completo) foi impressa em nenhum momento — apenas contagens, schemas e agregados. Os scripts de auditoria ficaram no diretório de scratchpad da sessão (fora do repositório), não foram commitados.

**Sessão 3 (fechamento pré-commit)**: **nenhum novo acesso ao Data Mart foi feito nesta sessão**, por instrução explícita — aguardando confirmação de que a credencial exposta na Sessão 1 foi rotacionada. A auditoria já não está mais integralmente pendente (schema, contagens totais, cobertura de UF e a taxa de overlap Shopee/ML já foram confirmadas por leitura real na Sessão 2 — ver §1), mas a comparação **linha a linha** dos 1.411 pedidos Shopee com overlap (itens/SKUs, quantidade, subtotal/GMV, taxas, frete, status e demais campos de negócio) **continua pendente** — só a ausência de mudança de `order_status` foi verificada até agora, o que não é suficiente para provar que os snapshots são idênticos em todos os campos relevantes. Essa comparação será feita assim que a rotação for confirmada.

**Sessão 4 (2026-07-08, credencial rotacionada e confirmada pelo usuário)**: Gates 4A/4B/4C/5 executados em modo read-only via módulo reutilizável `pipelines/reconciliation/audit_marketplace_region_sources.py` (executado com `python -m pipelines.reconciliation.audit_marketplace_region_sources`, a partir da raiz do repo — mesmo mecanismo já validado em `pipelines/common/db.py`). Confirmado antes de qualquer query: `SELECT 1 = 1 -> True`, `pg_is_in_recovery() -> True`. Nenhuma URL, credencial, PII ou `order_id` foi impressa — só agregados. Resultado: a comparação campo a campo pendente da Sessão 3 foi concluída (§1.1, agora **confirmada**, não mais provisória) e a causa raiz da baixa cobertura ML de barbours foi determinada objetivamente (§1.2, não é mais hipótese). Nenhuma Gold foi criada, nenhum DDL/DML executado, nenhum commit/push/deploy.

## 1. Achados confirmados por leitura direta (2026-07-08)

### 1.1 Shopee — `silver.stg_shopee_order_item_snapshots`

| Achado | Valor real |
|---|---|
| Total de linhas | **383.298** (bate exatamente com o já documentado) |
| `delivery_state` preenchido | **100%** (0 nulos/vazios) |
| UFs distintas | **27** |
| Formato de `delivery_state` | Nome completo do estado, **não sigla** (ex: `"Minas Gerais"`), com **corrupção de encoding confirmada** em nomes acentuados (`"S�o Paulo"`, `"Paran�"`, `"Goi�s"`, `"Esp�rito Santo"`, `"Cear�"`, `"Maranh�o"`, `"Par�"`, `"Piau�"`, `"Rond�nia"`, `"Amap�"`) — a normalização precisa de uma tabela de mapa fixa nome→sigla que já espera essa corrupção (ex: casar por prefixo `"S�o Paulo"` → `SP`), não uma comparação de string exata pós-correção de acentos. |
| Pedidos distintos (`order_id`×`brand`) | 361.101 |
| Pedidos com overlap de snapshot (aparecem em >1 `file_id`) | **1.411 (0,39%)** — bem menor que o pior caso hipotético |
| Status muda entre os snapshots dos pedidos com overlap | **0 de 1.411** — **todos os overlaps são re-exports idênticos**, nenhum pedido "progride" de status entre exports. Isso simplifica bastante a regra de dedup (ver §2). |
| Pedidos multi-item (>1 linha de SKU no mesmo `file_id`) | 15.942 de 362.512 combinações pedido×file (**4,40%**), máximo 11 linhas por pedido |
| Cobertura dos campos de frete (`buyer_paid_shipping_fee`, `estimated_shipping_fee`, `reverse_shipping_fee`) | **100% não-nulos** nos três; `reverse_shipping_fee` é diferente de zero em apenas 370 linhas (0,1% — coerente com só devoluções terem frete reverso) |

### 1.1a Comparação campo a campo (Sessão 4, CONFIRMADA)

Os 1.411 pedidos com overlap foram comparados no grão pedido × arquivo (`brand, order_id, file_id`, agregando as linhas de SKU de cada combinação) em **todos** os campos pedidos: contagem de linhas, multiset de SKU+variação+quantidade (via assinatura ordenada, preserva duplicidade física), `quantity`/`returned_quantity`, `product_subtotal`, `order_amount`, `order_grand_total`, `buyer_paid_shipping_fee`, `estimated_shipping_fee`, `reverse_shipping_fee`, `transaction_fee`, `commission_fee_gross/net`, `service_fee_gross/net`, `order_status`, `return_refund_status`, `delivered_date`, `cancel_completed_date`, `delivery_city`, `delivery_state`.

**Resultado: 1.411 de 1.411 pedidos (100%) são `exatamente_equivalente`** — zero divergência em qualquer campo monitorado, em qualquer um dos pedidos. GMV envolvido nos overlaps: R$ 104.832,11 (soma de `order_amount`, grão pedido).

Monotonicidade `file_id` × `raw_ingested_at` (pré-requisito para usar `MAX(file_id)` como critério de snapshot mais recente): **confirmada, 0 exceções** — em nenhum dos 1.411 pedidos o `file_id` máximo diverge do `raw_ingested_at` máximo, e não há nenhuma inversão `file_id`/`raw_ingested_at` entre os 120 arquivos de `raw.shopee_ingestion_file` (todos com `ingestion_status = 'success'`). **Ressalva**: `source_modified_at` (metadado do arquivo de origem) tem 33 inversões pontuais (minutos de diferença, mesmo dia, mesmo lote de importação) em relação a `file_id` — não afeta a conclusão porque `raw_ingested_at` (nosso próprio relógio de carga, que é o que importa para "mais recente no sistema") é perfeitamente monotônico, mas registra-se como cuidado para snapshots futuros que eventualmente divirjam de conteúdo.

**Conclusão**: `order_status` idêntico já era um indício; a comparação completa confirma que os overlaps são 100% re-exports idênticos em todos os campos de negócio, não apenas no status. A regra de dedup abaixo (§2) está **aprovada**, não mais provisória.

**Reconciliação antes/depois do dedup** (grão pedido, `order_amount`): antes (com duplicidade de overlap, 362.512 combinações pedido×arquivo) GMV = R$ 21.440.202,60; depois (dedupicado, 361.101 pedidos) GMV = R$ 21.335.370,49. Diferença = R$ 104.832,11 — bate exatamente com o GMV dos 1.411 overlaps, confirmando que o dedup remove só a duplicidade de re-export, sem tocar em nenhum outro pedido.

### 1.2 Mercado Livre — join `stg_ml_orders` × `stg_ml_shipments` × `stg_ml_shipment_costs`

| Achado | Valor real |
|---|---|
| Total de linhas em `silver.stg_ml_orders` | 345.132 |
| Total de linhas em `silver.stg_ml_shipments` | 236.320 |
| Total de linhas em `silver.stg_ml_shipment_costs` | 244.193 (42 `shipment_id` com 2 linhas de custo — duplicidade pequena, a tratar) |
| **UF em `stg_ml_shipments` (grão de envio)** | **100% (236.320 de 236.320)** — confirma a entrada original (~233.772), diferença de ~1% plausível por crescimento da tabela desde o número original |
| **Custo em `stg_ml_shipments` (grão de envio, join direto com `shipment_costs`)** | 234.376 de 236.399 (**99,1%**) — também confirma a ordem de grandeza da entrada original (~231.751) |
| **Pedidos (`stg_ml_orders`) sem `shipping_id`** | Apenas 53 (50 cancelados + 3 pagos) — desprezível |
| Formato de `receiver_state` | Já vem como **sigla prefixada `BR-XX`** (ex: `BR-SP`), não precisa de mapa nome→sigla — só remover o prefixo `BR-`. `receiver_state_name` tem a mesma corrupção de encoding do Shopee. |
| `sender_cost`: nulos / zeros / média / min / max | 0 nulos; 51.064 zeros (20,9%); média R$9,60; min R$0; max R$178,65 |
| Packs (múltiplos pedidos por `shipment_id`) | 308.708 shipments com 1 pedido; até 14 pedidos em 1 shipment (packs multi-pedido do ML) |

**⚠️ Achado importante que corrige a entrada original**: a cobertura "quase 100%" documentada é real **no grão de envio (shipment)**, mas a Gold regional precisa do grão de **pedido** (é onde o GMV mora). Medindo a cobertura órfã por pedido:

| Marca | Total de pedidos | Com UF + custo | % |
|---|---|---|---|
| barbours | 143.112 | 67.638 | **47,26%** |
| kokeshi | 68.459 | 56.330 | 82,28% |
| lescent | 50.110 | 45.375 | 90,55% |
| rituaria | 83.451 | 79.478 | 95,24% |
| **Total** | **345.132** | **250.861 → 248.821 com custo** | **72,09%** |

**barbours — a marca dominante em GMV — tem menos da metade dos pedidos com UF+custo associados.** Isso não invalida o modelo, mas é uma limitação real e severa que precisa aparecer com destaque na API (`shipping_cost_coverage_pct` por marca/UF) e na interface, não escondida atrás de uma média geral de 72%.

### 1.2a Causa raiz confirmada (Sessão 4) — gap de ingestão de shipments, não de pedidos

Pré-requisitos de cardinalidade confirmados antes de qualquer classificação: **zero** `brand,order_id` duplicado em `stg_ml_orders`, **zero** `brand,shipment_id` duplicado em `stg_ml_shipments`/`stg_ml_shipment_costs`, **zero** join ambíguo (`shipping_id` → mais de um `shipment_id`), e **GMV idêntico antes/depois do LEFT JOIN** (R$ 28.504.841,35 = R$ 28.504.841,35) — o join não multiplica pedidos nem GMV.

Classificação por bucket (pedidos pagos, grão pedido, fonte `silver.stg_ml_*`): `shipping_id_ausente` é desprezível em todas as marcas (0–4 pedidos). O grosso da lacuna é **`shipment_ausente`** — o pedido tem `shipping_id` preenchido, mas não existe nenhuma linha correspondente em `stg_ml_shipments`.

**Quebra mensal de barbours (achado central)**: a lacuna não é uniforme no tempo — é um gap estrutural concentrado em um período específico:

| Mês (pedido) | Pedidos pagos | `completo` | `shipment_ausente` |
|---|---|---|---|
| 2025-07 a 2025-10 | ~8.400 | ~8.400 (100%) | 0 |
| 2025-11 | 10.824 | 4.513 (42%) | 6.311 (58%) |
| 2025-12 | 5.840 | 1.974 (34%) | 3.865 (66%) |
| 2026-01 | 9.768 | 165 (2%) | 9.602 (98%) |
| 2026-02 | 13.014 | 52 (0,4%) | 12.961 (99,6%) |
| 2026-03 | 21.594 | 114 (0,5%) | 21.476 (99,5%) |
| 2026-04 | 21.367 | 1.053 (5%) | 20.314 (95%) |
| 2026-05 | 27.281 | 26.335 (96,5%) | 945 (3,5%) |
| 2026-06 em diante | ~20.000+ | ~100% | ~0 |

Cruzando com `raw.ml_shipments` (shipments por mês de criação do próprio shipment, não do pedido): barbours teve **apenas 153 shipments em janeiro/2026, 50 em fevereiro/2026 e 110 em março/2026** — contra 9.768/13.014/21.594 pedidos pagos nesses mesmos meses. Nenhum outro mês (nem antes, nem depois) tem esse descolamento; nenhuma outra marca apresenta um gap comparável (kokeshi/lescent têm gaps pequenos e decrescentes, concentrados em 2025-04 a 2025-10, típicos de ramp-up normal de integração — não um buraco de 5 meses). Os `shipping_id` dos pedidos sem match nesse período são numericamente contíguos com os `shipment_id` que existem imediatamente antes e depois (a sequência de IDs do ML é monotônica no tempo) — ou seja, os shipments **nunca foram ingeridos**, não é um problema de casamento de ID.

**Causa raiz confirmada**: gap de ingestão da API de Shipments do ML **especificamente para barbours, entre novembro/2025 e março/2026** (~5 meses) — os pedidos continuaram sendo criados e integrados normalmente (99%+ com `shipping_id` preenchido), mas os registros de shipment correspondentes praticamente não foram capturados nesse período. O gap encolhe abruptamente a partir de abril/2026 e está resolvido (>96% completo) desde maio/2026. Isso **descarta** as hipóteses de: pedidos sem `shipping_id` (irrelevante, <10 casos), erro de cardinalidade/join (descartado, 0 casos ambíguos), filtro de status (o bucket usa só `status='paid'`, igual em todos os meses). **Confirma** "shipments não ingeridos" como causa, restrita a uma marca e a uma janela temporal específica — não uma limitação estrutural permanente do modelo de dados.

### 1.2c Silver vs Raw ML — auditoria quantificada e DECISÃO (Sessão 5)

`silver.stg_ml_shipments`/`stg_ml_shipment_costs` estão desatualizados em relação a `raw.ml_shipments`/`raw.ml_shipment_costs`. Isolando o efeito (população de pedidos fixa em `silver.stg_ml_orders`, variando só o lado shipment/cost entre silver e raw):

| Marca | Cobertura completa (silver) | Cobertura completa (raw) | Gap | GMV recuperável usando raw | Custo de frete: silver vs raw |
|---|---|---|---|---|---|
| barbours | 47,92% | 51,53% | 3,61 p.p. | R$ 477.752,25 | R$ 823.498,15 vs R$ 823.518,35 (~igual) |
| kokeshi | 82,39% | 89,59% | 7,20 p.p. | R$ 352.986,32 | R$ 340.565,71 vs R$ 367.618,50 (**-7,4%** no silver) |
| lescent | 90,60% | 98,97% | 8,37 p.p. | R$ 255.419,90 | R$ 330.323,24 vs R$ 361.516,75 (**-8,6%** no silver) |
| rituaria | 95,28% | 100,00% | 4,72 p.p. | R$ 389.332,86 | R$ 1.003.227,78 vs R$ 1.016.377,51 (**-1,3%** no silver) |

**O gap NÃO é uniforme no tempo** — é concentrado nos meses mais recentes (abril–junho/2026): ex. kokeshi abril/2026 tem 2.207 de 2.286 pedidos pagos (96,5%) afetados; lescent abril/2026 tem 1.509 de 1.597 (94,5%). Meses de 2025 têm divergência residual pequena (1 a 163 pedidos). Isso é o padrão esperado de **atraso de sincronização "cauda"** (silver sempre está alguns dias/semanas atrás de raw na janela mais recente) — não um buraco estrutural permanente como o de barbours em §1.2a/1.2b (esse já aparece igualmente em `raw.*`, então não é staleness).

**Achado adicional a favor de `raw.*`**: `raw.ml_shipment_costs` não tem nenhum `(brand, shipment_id)` duplicado (0 grupos, confirmado nesta auditoria) — mais limpo que a pequena duplicidade (42 `shipment_id` com 2 linhas) já registrada para `silver.stg_ml_shipment_costs` no Gate 4 original.

**DECISÃO recomendada e adotada no draft**: usar **`raw.ml_shipments`/`raw.ml_shipment_costs`** (não `silver.stg_ml_*`) como fonte do lado shipment/cost do transform da Gold regional para ML. A diferença é **material** (3,6–8,4 p.p., R$255k–478k de GMV e até 8,6% do custo de frete por marca) — grande demais para tratar como "irrelevante" — mas **não exige bloquear o Gate 6**: como o gap está isolado à janela de sincronização recente e `raw` é comprovadamente um superconjunto de `silver` para shipments/custos nesta auditoria (0 casos de shipment/custo presente em silver e ausente em raw, nas duas direções verificadas), apontar o transform para `raw.*` resolve o problema pela causa, sem esperar nenhuma correção de pipeline. Pedidos (`orders`) podem continuar vindo de `silver.stg_ml_orders` — a divergência ali é desprezível (110 de 345.960, 0,03%) e não afeta este raciocínio; se preferir simplicidade operacional, usar `raw.ml_orders` também é igualmente válido, já que são idênticos em cardinalidade.

**Risco residual a monitorar** (não bloqueia): esta conclusão vale para o snapshot lido nesta sessão. Se o atraso real de sincronização do silver crescer (ex: pipeline de sync pausado por mais tempo), o mesmo raciocínio deve ser reexecutado — `pipelines/reconciliation/audit_marketplace_region_sources.py::audit_ml_staleness_impact_by_brand` foi criada para isso, sem precisar repetir SQL ad-hoc.

### 1.2d Timezone — auditoria e regra recomendada (Sessão 5)

Campos `timestamp without time zone` (`date_created` em `raw.ml_orders`/`silver.stg_ml_*`; `order_created_at`/`paid_at`/etc. em `silver.stg_shopee_order_item_snapshots`) — investigado se já representam horário de Brasília (BRT) ou UTC sem timezone anexado.

**Método 1 — relógio ao vivo**: no momento da consulta, `now() AT TIME ZONE 'UTC'` = 21:47:58 e `now() AT TIME ZONE 'America/Sao_Paulo'` = 18:47:58 (2026-07-08). `MAX(date_created)` em `raw.ml_orders` (tabela viva, ingestão contínua) = 17:44:53 — **~1h03 atrás do relógio BRT**, mas **~4h03 atrás do relógio UTC**. Para uma tabela que recebe pedidos continuamente, o pedido mais recente estar a ~1h do "agora" é plausível; estar a ~4h não é (implicaria um atraso de ingestão enorme e constante). Isso indica que `date_created` já está em **BRT**, não UTC.

**Método 2 — padrão de horário de compra**: distribuição por hora do dia de `date_created` (ML) e `order_created_at` (Shopee) mostra vale claro nas 2h–4h (madrugada) e platô alto das 9h às 21h, subindo a partir das 5h–6h — exatamente o padrão esperado de compra de varejo **em horário local brasileiro** (poucas compras de madrugada, volume alto ao longo do dia até a noite). Se os campos estivessem em UTC (BRT+3h), esse vale apareceria deslocado para as 5h–7h (madrugada BRT + 3h), o que **não é o que se observa** — o vale está exatamente nas 2h–4h, sem deslocamento.

**Conclusão (as duas evidências convergem)**: os campos `timestamp without time zone` de pedidos ML e Shopee já representam **horário local de Brasília, sem necessidade de conversão**. `date_created::date` (ou `order_created_at::date`) pode ser usado diretamente como a data de competência BRT na Gold regional — **não fazer** `AT TIME ZONE` nem qualquer cast adicional; fazer isso hoje introduziria um deslocamento artificial de 3h que não existe no dado. Colunas genuinamente `timestamptz` (ex: `raw_ingested_at`, `raw.shopee_ingestion_file.ingested_at`) continuam corretamente conversíveis via `AT TIME ZONE 'America/Sao_Paulo'` quando necessário (comportamento padrão do Postgres para esse tipo, não precisa de regra especial).

**Ressalva**: esta conclusão vale para os pedidos observados nesta sessão (~1 ano de histórico, ambos os marketplaces). Não foi auditado se a fonte já teve algum período com regra de timezone diferente (ex: migração de fuso na integração) — se a Gold regional aplicar isso a dados históricos muito mais antigos que os auditados, vale reconfirmar.

### 1.2b Decisão de produto — ML Barbours nov/2025–mar/2026 — **DECIDIDA: Opção A**

A causa raiz (§1.2a) está confirmada. Três opções foram avaliadas (manter com aviso / excluir ou marcar não confiável / bloquear a Gold até backfill); **decisão tomada pelo Mário: Opção A — manter no histórico, expor aviso/cobertura baixa, não bloquear a Gold inteira por esse intervalo.**

| Opção | Descrição | Trade-off | Status |
|---|---|---|---|
| **A. Manter com aviso de cobertura baixa** | Incluir os pedidos de barbours nov/2025–mar/2026 na Gold normalmente, com `uf_known_orders`/`shipping_cost_covered_orders` refletindo a lacuna real (baixos nesse intervalo) — a API deriva `uf_fill_pct`/`shipping_cost_coverage_pct` naturalmente baixos nesses meses e expõe `coverage_warning`/`coverage_level` explícitos (ver §6) | Mantém a série histórica completa e "honesta" (mostra o buraco em vez de escondê-lo); exige que quem consumir a API/dashboard trate `coverage_warning` na apresentação (não é opcional) | **✅ DECIDIDA** |
| B. Excluir ou marcar como não confiável | Não materializar linhas de barbours nesse intervalo, ou com flag `confiavel=false` separado | Evita soma equivocada, mas cria um "buraco" na série que pode ser confundido com ausência de vendas | Rejeitada |
| C. Bloquear a Gold até backfill dos shipments | Não aplicar a Gold regional (nem para as outras marcas/canais) até a origem (ML) fazer backfill retroativo | Mais conservador, mas depende de ação fora do controle deste projeto; bloquearia indefinidamente canais/marcas já confiáveis | Rejeitada |

**Consequência direta no design**: nenhuma coluna nova é necessária no schema (§3) — os numeradores/denominadores já capturam a lacuna nos dados; o que muda é que o **contrato de API** (§6) agora tem a obrigação explícita de nunca deixar um consumidor tratar um período/marca de baixa cobertura como se fosse dado completo, via `coverage_warning`/`coverage_level` calculados a partir desses mesmos numeradores/denominadores — nunca um número novo armazenado.

### 1.3 TikTok — confirmação de ausência de UF

Busca exaustiva em **todas as ~65 tabelas/views** com `tiktok` no nome (schemas `raw`, `api`, `silver`, `gold`) por qualquer coluna candidata a endereço (`state`, `uf`, `address`, `city`, `region`, `zip`, `cep`, `province`): **nenhuma coluna de endereço encontrada**. Os únicos falsos-positivos foram `statement_id`/`statement_time`/`statement_date` (liquidações financeiras, não endereço). **Confirmado**: TikTok não tem, em nenhuma camada do Data Mart, um caminho para UF do comprador.

## 2. Regra de dedup Shopee — **APROVADA** (Sessão 4, confirmada campo a campo)

```sql
-- APROVADA em 2026-07-08 apos comparacao campo a campo dos 1.411 pedidos
-- com overlap (itens/SKUs, quantidade, subtotal/GMV, taxas, frete, status,
-- datas, geografia): 100% exatamente_equivalente, 0 divergencias. Ver
-- pipelines/reconciliation/audit_marketplace_region_sources.py
-- (audit_shopee_snapshot_equivalence) e docs/regional_design_draft.md §1.1a.
--
-- MAX(file_id) e criterio deterministico e monotonico com raw_ingested_at
-- (0 excecoes em 1.411 pedidos, 0 inversoes em 120 arquivos) — confirmado,
-- nao mais hipotese.
SELECT DISTINCT ON (order_id, brand) *
FROM silver.stg_shopee_order_item_snapshots
ORDER BY order_id, brand, file_id DESC, raw_ingested_at DESC
```

A escolha do snapshot vigente deve ocorrer no grão **pedido × arquivo** (não por linha de SKU isolada) e, uma vez escolhido o `file_id` vencedor para aquele pedido, **todas** as linhas de SKU daquele snapshot devem ser mantidas juntas — nunca misturar linhas de `file_id` diferentes para o mesmo pedido. Isso é o que já preserva pedidos multi-item (15.942 de 362.512 combinações pedido×file, 4,40% dos casos, até 11 linhas de SKU por pedido — ver §1.1).

## 3. Draft do modelo — `gold.marketplace_region_daily`

**Grão explícito: `date × marketplace_id × brand (via loja_id) × uf`.** `loja_id` é FK de `dim_loja` e já é 1:1 com marca (`apice=1, barbours=2, kokeshi=3, lescent=4, rituaria=5` — mesmo mapa usado em `pipelines/transforms/ml_gestao_diaria.py:BRAND_TO_LOJA`); não há um `loja_id` compartilhado por duas marcas, então `loja_id` já carrega a granularidade de marca sem precisar de uma coluna `brand` redundante. DDL completa (draft, não aplicada) em `db/sql/gold/marketplace_region_daily_draft.sql`.

| Campo | Tipo | Observação |
|---|---|---|
| `date` | date | Data de negócio (não a data da carga) — ver risco de timezone em §8 |
| `marketplace_id` | int | FK `dim_marketplace` |
| `loja_id` | int | FK `dim_loja` — ≡ marca, 1:1 (ver acima) |
| `uf` | char(2) | Sigla oficial ou `'XX'` = "Não identificada" — nunca descartar linha por UF ausente |
| `gmv` | numeric | |
| `orders` | int | |
| `units_sold` | int | |
| `canceled_orders` | int | |
| `returned_orders` | int | |
| `avg_ticket` | numeric | Calculado, não armazenado como fonte de verdade |
| `seller_shipping_cost` | numeric | Custo do frete pago pelo seller (`sender_cost` no ML, confirmado por lineage em §4C). **Sem equivalente Shopee** — nunca inventar |
| `buyer_shipping_fee` | numeric | Frete cobrado do comprador |
| `estimated_shipping_fee` | numeric | Estimativa de frete (quando a fonte distinguir de valor cobrado) |
| `reverse_shipping_fee` | numeric | Frete de devolução, quando aplicável |
| `uf_known_orders` | int | Numerador de `uf_fill_pct` — pedidos do grão com UF identificada (≠ `XX`) |
| `uf_eligible_orders` | int | Denominador de `uf_fill_pct` — total de pedidos elegíveis do grão (inclui os que caíram em `XX`) |
| `shipping_cost_covered_orders` | int | Numerador de `shipping_cost_coverage_pct` — pedidos com custo de frete associado (join shipment+cost resolvido) |
| `shipping_cost_eligible_orders` | int | Denominador de `shipping_cost_coverage_pct` — total de pedidos pagos do grão |
| `source_updated_at` | timestamptz | Frescor da fonte de origem |

**Por que numerador/denominador, não percentual pronto**: `uf_fill_pct`/`shipping_cost_coverage_pct` são **derivados na API** somando os numeradores e denominadores em qualquer agregação (Brasil/marca/canal/período) — nunca fazendo média dos percentuais já calculados. Média de percentual é matematicamente errada ao agregar grãos com denominadores diferentes (ex: agregar um dia com 10 pedidos e 50% de cobertura com outro dia com 10.000 pedidos e 95% de cobertura não pode dar "72,5%" — o segundo dia domina a soma real). Essa é a mudança de design mais importante desta rodada em relação ao draft anterior (que armazenava só `shipping_cost_coverage_pct` como percentual).

**Regra explícita**: não criar um único `total_shipping_cost` agregando canais até que as semânticas de `buyer_shipping_fee`/`estimated_shipping_fee`/`reverse_shipping_fee` estejam comprovadamente equivalentes entre Shopee e ML (ver `docs/data_contracts.md` seção sobre `total_settlement`/`total_fees` — já houve um caso real de campos com nomes parecidos e semânticas diferentes entre canais). Confirmado nesta auditoria (§4C): Shopee **não tem** nenhum campo equivalente a `seller_shipping_cost`/`sender_cost` — a ausência é estrutural, não uma omissão a corrigir.

## 4. Regras de reconciliação e qualidade (draft)

1. **27 UFs oficiais + bucket `'XX' = Não identificada`** — nunca descartar uma linha por UF ausente/inválida; sempre bucketizar. Para Shopee, mapear pelo nome corrompido conhecido (lista real em §1.1); para ML, apenas remover o prefixo `BR-` de `receiver_state`.
2. **Normalização determinística de UF** — tabela de referência fixa, nunca fuzzy matching; cada variação de encoding tratada como entrada explícita do mapa, documentada.
3. **Soma das UFs deve bater com o total nacional do mesmo escopo** (mesma marca/canal/mês) já publicado em `marts.fact_marketplace_daily_performance` — diff esperado é zero; qualquer diff > 0 bloqueia a publicação da carga (mesmo padrão de gate usado no Bug 8 do Shopee, `docs/backlog.md`).
4. **Sem pedidos duplicados**: a chave de negócio (pedido, não linha de SKU) aparece uma única vez por dia × UF; pedidos multi-item preservados (soma das linhas de SKU do pedido).
5. **Custos nunca negativos**: `CHECK` explícito excluindo `NaN` (`'NaN'::numeric >= 0` é `TRUE` em Postgres — não confiar só em `>= 0`), já implementado no draft de DDL.
6. **TikTok explicitamente sem cobertura**: linha (ou ausência de linha) documentada como tal na resposta da API, nunca como GMV=0 por UF.
7. **Cobertura ML por pedido exposta por marca** — não publicar um `shipping_cost_coverage_pct` só nacional quando a variação por marca é de 47% a 95% (achado real, §1.2); a resposta da API deve permitir ver a quebra por marca.
8. **Incrementalidade e idempotência**: `UNIQUE(date, marketplace_id, loja_id, uf)` com `ON CONFLICT DO UPDATE`.
9. **Frescor**: expor `source_updated_at` da Gold e comparar com o frescor da Silver/Raw de origem.
10. **Rollback e auditoria de carga**: registrar em `audit.source_sync_run` como as demais cargas.

## 5. Proposta de pipeline (draft, não implementado)

```text
Silver Shopee (snapshots, dedup por MAX(file_id)) ──┐
silver.stg_ml_orders/shipments/shipment_costs ──────┼──► transform regional (Data Mart) ──► gold.marketplace_region_daily (Data Mart)
TikTok (sem UF, linha "sem cobertura") ──────────────┘                                        │
                                                                                                 ▼
                                                                          sync incremental idempotente
                                                                                                 │
                                                                                                 ▼
                                                                       marts.fact_marketplace_region_daily (Neon)
                                                                                                 │
                                                                                                 ▼
                                                                        API (endpoints abaixo) ──► /regioes
```

- **Sync Gold → Neon**: mesmo padrão de `pipelines/ingestion/daily_performance.py` (upsert por chave única, modos incremental/backfill, registro em `audit.source_sync_run`). Necessário porque a API em produção não acessa o Data Mart pela VPN.
- **Tabela Neon proposta**: `marts.fact_marketplace_region_daily`, mesma estrutura do draft acima, populada só depois de o Gold no Data Mart estar validado.

## 6. Contratos de endpoint (draft)

Todos sob `/api/v1/performance/regioes/*`, reutilizando o mesmo `filters_query` (`channels`/`brands`/`date_from`/`date_to`) já implementado e documentado em `docs/filtros_globais_contrato.md` — **não introduzir um nome de parâmetro novo** (`marketplaces`) quando `channels` já é o contrato estabelecido (com `marketplace` aceito como alias legado); "marketplaces" e "channels" se referem ao mesmo conceito nesta API.

### Parâmetros aceitos

| Parâmetro | Tipo | Igual ao contrato de filtros globais? |
|---|---|---|
| `channels` | `"all"` \| canal isolado \| lista separada por vírgula | Sim, reaproveitado (`marketplace` aceito como alias legado) |
| `brands` | lista de `brand_key` separada por vírgula | Sim, reaproveitado |
| `date_from` / `date_to` | `YYYY-MM-DD`, inclusivos | Sim, reaproveitado (`ref_month` aceito como alias legado, igual às outras 5 telas) |
| `uf` | sigla isolada, lista separada por vírgula, ou `XX` | **Novo**, específico de `/regioes` — filtra o resultado a UF(s) específicas; omitido = todas as 27 + `XX` |

Não existe um parâmetro `compare` aqui nesta primeira versão — comparação MoM por UF fica fora do escopo deste draft (pode ser adicionado depois, reaproveitando `resolve_previous_period`, sem redesenhar o contrato de filtros).

### Endpoints

| Endpoint | Descrição | Grão de resposta |
|---|---|---|
| `GET /regioes/resumo` | KPIs agregados por UF no período filtrado | UF × (gmv, orders, cancel_rate, shipping_cost, coverage **por marca**) |
| `GET /regioes/ranking` | Ranking de UFs por GMV (ordenável) | idem, paginado |
| `GET /regioes/tendencia` | Série temporal por UF (top N UFs) | date × uf × gmv |

### Campos de resposta (comuns aos 3 endpoints)

Sempre presentes: `filters` (echo dos parâmetros recebidos, incluindo `uf`), `date_from`/`date_to`, `refreshed_at`. Cada linha de UF traz `gmv`, `orders`, `units_sold`, `canceled_orders`, `returned_orders`, `avg_ticket` (calculado na API, nunca armazenado), e os campos de frete (`seller_shipping_cost`, `buyer_shipping_fee`, `estimated_shipping_fee`, `reverse_shipping_fee` — só os que cada canal de fato tiver).

### Campos de qualidade/cobertura (obrigatórios, nunca opcionais)

`uf_fill_pct` e `shipping_cost_coverage_pct` são **calculados na API a partir da soma dos numeradores/denominadores** (`uf_known_orders`/`uf_eligible_orders`, `shipping_cost_covered_orders`/`shipping_cost_eligible_orders` — ver §3), nunca lidos como percentual pronto do banco, e nunca uma média que esconda o pior caso (ex: barbours 47% escondido atrás de uma média nacional de 72%). `uf_fill_pct` (UF preenchida na fonte) e `dedup_pct` (pedidos Shopee sem overlap de snapshot a resolver) são **dimensões distintas** e não devem ser colapsadas em um único número — Shopee tem UF preenchida em 100% dos casos, o que é uma questão completamente separada de haver ou não overlap de export a resolver na dedup.

**`coverage_level` e `coverage_warning` (novos nesta rodada, implementam a decisão §1.2b)** — derivados na API a partir dos mesmos numeradores/denominadores, **nunca armazenados na Gold** (mesmo princípio de "nunca guardar percentual pronto" — thresholds podem mudar sem exigir migração):

| `coverage_level` | Regra (sobre `min(uf_fill_pct, shipping_cost_coverage_pct)` do grão marca/período) | `coverage_warning` |
|---|---|---|
| `"alta"` | ≥ 80% | `false` |
| `"media"` | 50%–79,9% | `true` |
| `"baixa"` | < 50% | `true` |
| `"sem_cobertura"` | Canal sem fonte de UF (só TikTok) | `true` |

Thresholds provisórios (80/50), a validar com o time de produto antes do Gate 6 — o importante é que existam como constante de código único (não espalhados em múltiplos endpoints), documentados aqui, e que `coverage_warning=true` sempre venha acompanhado de `coverage_level` explicando o motivo (nunca um boolean sozinho sem contexto).

**Regra de ranking (`GET /regioes/ranking`)**: uma UF/marca/período com `coverage_level` `"baixa"` ou `"sem_cobertura"` **nunca deve aparecer misturada sem marcação num ranking nacional ordenado por GMV** — ela deve vir com `coverage_warning=true` e `coverage_level` na própria linha do ranking (não só num bloco agregado separado), e a interface (§7) deve decidir explicitamente entre (a) mostrar a linha com um badge de alerta visível junto à posição no ranking, ou (b) segregar essas linhas numa seção "cobertura incompleta" fora do ranking principal — qualquer uma das duas é aceitável, o que **não é aceitável** é a linha aparecer ranqueada como se fosse comparável a uma UF/marca de cobertura alta sem nenhuma marcação. Isso vale especialmente para barbours nov/2025–mar/2026 (§1.2b): nesse período, um ranking nacional por UF que inclua barbours sem aviso subestimaria sistematicamente a posição relativa das UFs onde barbours vende mais.

```json
{
  "tiktok": { "coverage": "sem_cobertura" },
  "ml": {
    "uf_fill_pct_by_brand": { "barbours": 47.26, "kokeshi": 82.28, "lescent": 90.55, "rituaria": 95.24 },
    "shipping_cost_coverage_pct_by_brand": { "barbours": 47.26, "kokeshi": 82.28, "lescent": 90.55, "rituaria": 95.24 },
    "coverage_level_by_brand": { "barbours": "baixa", "kokeshi": "alta", "lescent": "alta", "rituaria": "alta" },
    "coverage_warning_by_brand": { "barbours": true, "kokeshi": false, "lescent": false, "rituaria": false },
    "note": "barbours nov/2025-mar/2026 tem cobertura estruturalmente baixa nesse periodo especifico (gap de ingestao de shipments na origem, ver docs/regional_design_draft.md secao 1.2a/1.2b) — mantido no historico por decisao de produto (Opcao A), nao e um erro deste endpoint"
  },
  "shopee": {
    "uf_fill_pct": 100.0,
    "dedup_pct": 99.61,
    "coverage_level": "alta",
    "coverage_warning": false,
    "note": "UF vem preenchida em 100% dos pedidos; dedup_pct e a fracao de pedidos SEM overlap de snapshot a resolver (0,39% tem overlap, ver secao 1.1) — sao duas metricas diferentes, nunca combinar em um so numero"
  }
}
```

### Comportamento para TikTok sem UF

TikTok **nunca aparece com linhas de UF** — nem `uf='XX'` com valores, nem GMV=0 por UF. O echo por canal sempre retorna `{"coverage": "sem_cobertura"}` explícito (como no exemplo acima), e as 3 rotas de `/regioes/*` devem: (a) se `channels` incluir `tiktok` isoladamente ou junto de outros canais, responder 200 com os outros canais normalmente e o bloco `tiktok.coverage = "sem_cobertura"` — nunca 422 (TikTok pode legitimamente estar selecionado nas outras telas do dashboard); (b) se `channels=tiktok` for o único canal pedido, responder 200 com todas as linhas de UF vazias/zeradas e `tiktok.coverage = "sem_cobertura"` como o único conteúdo relevante — nunca inventar linhas de UF para TikTok mesmo que o filtro peça exclusivamente esse canal.

## 7. Esboço de interface (`/regioes`)

MVP: **tabela ordenável + barras horizontais por UF**, reaproveitando `useSortableTable`/`SortableHeader`. **Sem mapa**. Filtros globais no mesmo padrão das 6 telas já entregues (`useGlobalFilters`). Estado vazio explícito quando uma UF não tiver dados no período. Indicador visual de cobertura baixa (ex: badge de alerta quando `coverage_pct` < 60%) para não deixar o usuário interpretar um número por UF como completo quando não é — especialmente relevante para barbours no ML.

## 4C. Definições de métricas — confirmadas via lineage real (Sessão 4)

Inspecionado o SQL real de `gold.ml_gestao_diaria` (é uma **VIEW**, não tabela materializada — definição obtida via `pg_get_viewdef`, não documentação de terceiros):

| Conceito | Definição confirmada na fonte |
|---|---|
| GMV (ML) | `SUM(total_amount)` de `raw.ml_orders` **filtrado por `status = 'paid'`** — não é `paid_amount` (esse campo é calculado na view mas não exposto) |
| Data de competência (pedido) | `date_created::date` do pedido (não `date_closed`) |
| Data de competência (frete) | `date_created::date` do **shipment** (não do pedido!) — ver achado crítico abaixo |
| `seller_shipping_cost` (ML) | `SUM(sender_cost)` de `ml_shipment_costs`, join por `shipment_id` — **confirma exatamente** a hipótese `seller_shipping_cost = sender_cost` já registrada no draft anterior |
| Status incluídos no cancelamento | `status = 'cancelled'` (pedido); shipments usam seu próprio `status` (`delivered`/`not_delivered`/`cancelled`/etc.), independente do status do pedido |
| Marcas | `barbours`, `kokeshi`, `lescent`, `rituaria` (mesmas 4 marcas Data Mart-side; `apice` fica de fora do ML, confirmado sem nenhuma linha) |
| Timezone | Colunas de data são `timestamp without time zone` tanto em `raw.*` quanto `silver.stg_ml_*` — **não confirmado** se já é horário de Brasília ou UTC sem timezone anexado; não investigado nesta sessão, ver risco §8 |

**Achado crítico de metodologia — `gold.ml_gestao_diaria` não faz join por pedido**: a view agrega pedidos por `(date_created::date do pedido, brand)` num CTE (`daily_orders`) e agrega shipments por `(date_created::date do PRÓPRIO SHIPMENT, brand)` num CTE separado (`daily_shipping`), depois faz `FULL JOIN` **pela data e marca**, não pelo pedido. Ou seja, `shipping_pct_of_gmv` no dashboard atual é *"custo de frete dos shipments criados no dia X" dividido por "GMV dos pedidos criados no dia X"* — dois numeradores de populações diferentes que só coincidem por estarem no mesmo dia/marca, **não** um join pedido→shipment real. Isso é estruturalmente diferente da cobertura por pedido que este design regional calcula (join direto `shipping_id = shipment_id`). Os dois números **não são comparáveis diretamente** — a Gold regional deve deixar isso explícito na documentação da API para não sugerir que está reproduzindo o mesmo `shipping_pct_of_gmv` do dashboard atual (não está, e o join por pedido é a versão mais correta para atribuir custo a uma UF específica).

**Shopee — fretes**: `buyer_paid_shipping_fee`, `estimated_shipping_fee`, `reverse_shipping_fee` são 3 colunas nativas e já estruturalmente separadas na fonte (confirmado no schema) — sem transformação a fazer. **Não existe** na fonte Shopee um campo equivalente ao `seller_shipping_cost`/`sender_cost` do ML (custo de frete pago pelo *seller*) — reforça a regra já registrada de não inventar um "custo total" cross-canal.

## Veredito de qualidade por canal (Sessão 4)

| Canal | Venda por UF | Custo por UF | Cancelamento por UF | Veredito |
|---|---|---|---|---|
| **Shopee** | UF 100% preenchida na fonte; dedup de snapshot **demonstrada e aprovada** (100% equivalentes, 0 divergência) | Frete Shopee tem 3 campos nativos, semântica própria, sem custo "seller" equivalente ao ML | `order_status`/`return_refund_status` 100% estáveis entre snapshots do mesmo pedido | **Confiável**, condicionado a corrigir a normalização de encoding de `delivery_state` (mapa fixo nome→sigla, corrupção conhecida documentada em §1.1) antes de aplicar |
| **Mercado Livre** | Cobertura por pedido varia de 47,3% (barbours) a 95,2% (rituária) usando `raw.*` (decidido em §1.2c); causa raiz confirmada (gap de ingestão de shipments, não estrutural, concentrado em nov/2025–mar/2026 só para barbours) | `seller_shipping_cost = sender_cost` confirmado por lineage; cobertura de custo segue a mesma cobertura de shipment (join único, usando `raw.ml_shipment_costs`) | Cardinalidade limpa (0 duplicados, 0 join ambíguo), GMV não multiplica no join | **Utilizável com aviso** — `shipping_cost_coverage_pct`/`coverage_level` expostos **por marca** (nunca média nacional de 72% escondendo barbours); período nov/2025–mar/2026 de barbours mantido no histórico com `coverage_warning=true` (decisão de produto tomada, §1.2b) |
| **TikTok** | Nenhuma coluna de endereço em ~65 tabelas/views investigadas exaustivamente | N/A | N/A | **Não confiável ainda** para regional — não é uma lacuna a preencher com heurística, é ausência estrutural confirmada; deve aparecer como `sem_cobertura` explícito, nunca GMV=0 por UF |

## 8. Riscos e perguntas em aberto (atualizado com dados reais)

1. ~~Cobertura ML por pedido em barbours é de apenas 47%~~ — **causa raiz confirmada (Sessão 4) + decisão tomada (Sessão 5, Opção A, §1.2b)**: gap de ingestão de shipments do ML restrito a barbours, nov/2025–mar/2026, mantido no histórico com `coverage_warning`/`coverage_level` explícitos no contrato de API (§6). Resolvido para efeito de design; nenhum backfill de origem foi feito nem é necessário para o Gate 6.
2. ~~Dedup de snapshot Shopee — regra ainda não confirmada~~ — **APROVADA (Sessão 4)**: 1.411 de 1.411 pedidos com overlap são 100% equivalentes em todos os campos de negócio auditados; `MAX(file_id)` é monotônico com `raw_ingested_at` (0 exceções). Ver §1.1a e §2.
3. **Semântica de frete não comprovadamente equivalente entre Shopee e ML** (mesmo problema já documentado para `total_settlement`/`total_fees`) — não somar `total_shipping_cost` cross-canal. **Confirmado** que Shopee não tem nenhum campo equivalente a `seller_shipping_cost`/`sender_cost` do ML (ver §4C) — a distinção não é apenas prudência, é ausência estrutural. Resolvido para efeito de design (regra permanente, não uma pendência).
4. **TikTok permanece estruturalmente sem UF, confirmado exaustivamente** — qualquer pedido de "totais por UF incluindo TikTok" deve ser recusado ou marcado como incompleto na resposta, nunca preenchido com heurística. Comportamento de contrato já especificado em §6.
5. **`gold.ml_gestao_diaria` não faz join por pedido** — o `shipping_pct_of_gmv` do dashboard atual agrega por (data do shipment, marca) e não é comparável ao `shipping_cost_coverage_pct` por pedido que a Gold regional propõe. Documentado em §4C — risco de confusão entre os dois números permanece, mitigado por documentação, não por código (nenhum dos dois vai mudar).
6. ~~`silver.stg_ml_orders`/`stg_ml_shipments` desatualizados em relação a `raw.ml_*`~~ — **quantificado e resolvido (Sessão 5, §1.2c)**: gap material (3,6–8,4 p.p., concentrado nos últimos 1–3 meses, padrão de atraso de sincronização). Decisão: usar `raw.ml_shipments`/`raw.ml_shipment_costs` no transform da Gold, não `silver.*`. Sem bloqueio de Gate 6.
7. ~~Timezone dos campos `timestamp without time zone` não confirmado~~ — **confirmado (Sessão 5, §1.2d)**: já em horário de Brasília (BRT), sem necessidade de conversão. `date_created::date`/`order_created_at::date` usados diretamente.
8. **Credencial do Data Mart** — rotação confirmada pelo usuário; acesso feito via `pipelines.common.db` (mecanismo já validado do repo), nunca imprimindo credenciais.
9. **Thresholds de `coverage_level` (80%/50%, §6) são provisórios** — não validados com o time de produto; ajustar antes do Gate 6 se necessário, mas não bloqueiam o design (é uma constante, não uma decisão estrutural).

## 9. Checklist de prontidão para o Gate 6A (aplicação)

| Item bloqueante | Status |
|---|---|
| Dedup Shopee aprovado por comparação campo a campo | ✅ Resolvido (§1.1a/§2) |
| Causa raiz da cobertura ML (barbours) confirmada | ✅ Resolvido (§1.2a) |
| Decisão de produto para o histórico de barbours | ✅ Decidida — Opção A (§1.2b) |
| Fonte ML para o transform (silver vs raw) | ✅ Decidida — usou `raw.ml_shipments`/`raw.ml_shipment_costs` (§1.2c) |
| Timezone dos timestamps naive | ✅ Confirmado — BRT nativo, sem conversão (§1.2d) |
| Schema com numerador/denominador (evita médias erradas) | ✅ Aplicado (`db/sql/gold/marketplace_region_daily_ddl.sql`) |
| Contrato de API com `coverage_warning`/`coverage_level` | ✅ Especificado (§6) — implementação do endpoint é Gate 6B |
| TikTok tratado como `sem_cobertura`, nunca GMV=0 por UF | ✅ Confirmado — 0 linhas na Gold aplicada (§10) |
| Regra "sem custo Shopee inventado equivalente ao ML" | ✅ Confirmada e documentada (§4C) |
| Autorização explícita para aplicar DDL/criar Gold | ✅ **Autorizada e executada (§10)** |
| Validação de thresholds de `coverage_level` com produto | ⚠️ Pendente, não bloqueante (constante ajustável sem migração, fica para o Gate 6B) |

**Gate 6A concluído.** Gate 6B (sync Neon, endpoints, frontend) permanece bloqueado até autorização separada.

## 10. Gate 6A — Resultado da aplicação real (executado com autorização explícita)

DDL aplicada (`db/sql/gold/marketplace_region_daily_ddl.sql`, 13 statements — 1 tabela, 3 índices, 9 comentários) e primeira carga (`pipelines/ingestion/gold_regional/loader.py::execute_first_load`) executadas em transações separadas, cada uma precedida por um preflight de escrita que confirmou: conexão no **primary** (`pg_is_in_recovery=false`), mesmo cluster físico da réplica de leitura, role sem superuser com permissão adequada em `gold`, e `gold.marketplace_region_daily` **não existente** antes do DDL.

### Contagens e estrutura

- **Total de linhas**: 33.343.
- **Constraints**: `chk_region_gmv_non_negative`, `chk_region_shipping_non_negative`, `chk_region_uf_valida` (todas `convalidated=true`), `uq_region_daily` (UNIQUE), `marketplace_region_daily_pkey`.
- **Índices**: `ix_region_daily_date`, `ix_region_daily_uf`, `ix_region_daily_loja` + os 2 implícitos de PK/UNIQUE.
- **Colunas**: as 20 da DDL, exatamente — nenhuma coluna de PII (CPF/nome/telefone/endereço/`order_id`/etc.) existe na tabela.
- **UF distintas presentes**: as 27 siglas oficiais + `XX` (28 valores) — nenhum estado ficou de fora do mapa.

### Reconciliação (recalculada da fonte no momento da carga, dentro da transação — nunca contra uma constante fixa)

| | Staging | Fonte recalculada | Diff |
|---|---|---|---|
| GMV Shopee (dedupicado) | R$ 21.335.370,49 | R$ 21.335.370,49 | R$ 0,00 |
| GMV ML (`status='paid'`) | R$ 28.700.027,29 | R$ 28.700.027,29 | R$ 0,00 |

Shopee (todas as 5 marcas): 310.495 pedidos, 336.265 unidades, 50.606 cancelados, 4.031 devolvidos/contestados.

### Cobertura ML por marca (agregada no período todo, `uf_fill_pct` = `shipping_cost_coverage_pct` porque os dois numeradores coincidem nesta carga)

| Marca (`loja_id`) | Pedidos | Cobertura |
|---|---|---|
| barbours (2) | 136.197 | **51,83%** |
| kokeshi (3) | 67.045 | 89,69% |
| lescent (4) | 47.844 | 98,98% |
| rituaria (5) | 81.764 | 100,00% |

**Barbours nov/2025–mar/2026, mês a mês (confirma a Opção A funcionando como desenhado — aparece como cobertura baixa, não como erro/exclusão)**:

| Mês | Pedidos | `uf_fill_pct` |
|---|---|---|
| 2025-11 | 10.130 | 42,26% |
| 2025-12 | 5.416 | 33,86% |
| 2026-01 | 9.036 | 1,72% |
| 2026-02 | 12.122 | 0,12% |
| 2026-03 | 19.784 | 0,27% |

### Integridade

Zero duplicidade em `(date, marketplace_id, loja_id, uf)`; zero nulos nas colunas obrigatórias; zero linha com numerador > denominador; **TikTok: 0 linhas** (confirmado, marketplace_id=1 ausente por completo).

### Validações não executadas nesta etapa (fora do escopo do Gate 6A)

Sync Data Mart → Neon, endpoints `/regioes/*`, qualquer mudança de frontend, e validação dos thresholds de `coverage_level` (80%/50%) com o time de produto — todos ficam para o Gate 6B.

Ver plano de implementação desta feature em `C:\Users\Notebook\.claude\plans\quiet-crafting-rain.md` para o contexto completo dos Gates 1–4. Módulo de auditoria reutilizável: `pipelines/reconciliation/audit_marketplace_region_sources.py` (testes com conexão falsa em `pipelines/tests/test_audit_marketplace_region_sources.py`).

## 11. Gate 6B.1 — sync Data Mart → Neon (implementado e testado, NÃO executado)

Diagnóstico somente leitura confirmou: `marts.fact_marketplace_region_daily` ainda não existe no Neon (slate limpo); `marts.dim_marketplace`/`marts.dim_loja` no Neon confirmam os mesmos IDs já usados em todo o Gate 6A (TikTok=1, ML=2, Shopee=3, apice=1, barbours=2, kokeshi=3, lescent=4, rituaria=5); `marts.dim_calendario` cobre 2024-01-01 a 2027-12-31 (cobre toda a Gold regional carregada); o role do `DATABASE_URL` tem `CREATE`/`USAGE` no schema `marts` (sem necessidade de secret dedicado de escrita, diferente do Data Mart — Neon não tem a distinção primary/réplica).

**Contrato Neon definido**: `marts.fact_marketplace_region_daily`, grão `date × marketplace_id × loja_id × uf`, mesmas 17 colunas de negócio da Gold regional (sem `id`/`ingested_at`, gerados no destino). Segue a convenção já estabelecida em `apps/api/alembic/versions/003_create_facts_and_audit.py` (FK reais para `dim_calendario(date)`, `dim_marketplace(marketplace_id)`, `dim_loja(loja_id)`; `CREATE TABLE IF NOT EXISTS`, diferente da regra "falha se já existir" do Gate 6A no Data Mart — cada camada segue a convenção já estabelecida no seu próprio diretório). `UNIQUE (date, marketplace_id, loja_id, uf)`. Mesmos `CHECK` de GMV/frete não-negativo e UF válida da Gold regional.

**Arquivos criados**:
- `apps/api/alembic/versions/005_create_fact_marketplace_region_daily.py` — DDL Neon (migration 005, não aplicada).
- `pipelines/sync_region_daily.py` — script de sync: lê a Gold regional (Data Mart, somente leitura), cria staging `TEMP` no Neon dentro de uma única transação, valida staging == fonte (agregados de todas as colunas numéricas + zero duplicidade + zero nulos obrigatórios), cria backup da tabela real se ela já tiver linhas (re-sync futuro), `TRUNCATE` + `INSERT` a partir da staging validada, valida real == staging pós-INSERT (`EXCEPT` bidirecional + agregados), só comita se tudo passar — qualquer exceção aciona rollback completo, sem retry automático. Registra `audit.source_sync_run`. Modo padrão `--diagnose` é somente leitura nos dois lados; `--sync` (escrita real) exige a flag explícita **e** `I_UNDERSTAND_THIS_WRITES_NEON_REGION_DAILY=1`.
- `pipelines/tests/test_sync_region_daily.py` — 33 testes com conexões falsas (nenhum banco real tocado): agregados/validações puras, caminho feliz (primeira carga e re-sync com backup), rollback em falha de lock/de escrita/de validação, idempotência, sanitização de erro, ausência de PII nas colunas de negócio, ausência de qualquer `DROP`/`TRUNCATE` fora do alvo, gating de `--sync` (flag + variável de ambiente).

**Validação rodada**: `pytest pipelines/tests` completo (908 passed), `compileall` nos 3 arquivos novos, `alembic upgrade head --sql` (gera o SQL da migration 005 offline, sem conectar a nenhum banco — cadeia 001→005 íntegra), `git diff --cached --check` (sem problemas de whitespace), varredura manual de segredos/PII nos 3 arquivos novos (únicos matches são texto explicativo e credenciais fake de teste, iguais ao padrão já usado em `test_gold_regional_ddl.py`).

**Gate 6B.1 concluído nesta forma** (implementação + testes, nenhuma escrita real) — ver §12 para o resultado da execução real do Gate 6B.2/6B.3, autorizada e executada em seguida.

## 12. Gate 6B.2/6B.3 — Resultado da execução real (executado com autorização explícita)

### Achado de preflight: drift do alembic

A tabela `alembic_version` não existia no Neon — as migrations 001–004 haviam sido aplicadas por fora do mecanismo do alembic em algum momento anterior, mas a estrutura já batia exatamente com o que elas descrevem (confirmado objeto a objeto antes de qualquer ação). Runbook de reconciliação, autorizado explicitamente pelo usuário entre três opções apresentadas: `alembic stamp 004` (grava apenas a tabela de controle, zero DDL de negócio) seguido de `alembic upgrade head` (roda somente a migration 005, confirmado via `alembic upgrade 004:head --sql` antes da execução real). Nenhuma das migrations 001–004 foi reexecutada.

### Preflight (somente leitura, antes de qualquer escrita)

- **Data Mart**: `gold.marketplace_region_daily` com 33.343 linhas, zero duplicidade, zero nulos obrigatórios, TikTok 0 linhas, zero colunas de PII (20 colunas, nenhuma suspeita).
- **Neon**: `marts.fact_marketplace_region_daily` não existia (OK para prosseguir); `dim_calendario` cobre 2024-01-01–2027-12-31; `dim_marketplace`/`dim_loja` com os mesmos IDs 1–5 já usados em todo o Gate 6A; role com `CREATE`/`USAGE` em `marts` e `audit`.

### Migration 005 aplicada

`alembic upgrade head` (real, com estado reconciliado em 004) criou `marts.fact_marketplace_region_daily` com exatamente as constraints/FKs/índices esperados: `chk_fmrd_gmv_non_negative`, `chk_fmrd_shipping_non_negative`, `chk_fmrd_uf_valida`, FKs para `dim_calendario(date)`/`dim_marketplace(marketplace_id)`/`dim_loja(loja_id)`, `UNIQUE(date, marketplace_id, loja_id, uf)`, PK, e os 3 índices `idx_fmrd_date`/`idx_fmrd_uf`/`idx_fmrd_loja_marketplace`. Tabela criada vazia (0 linhas), confirmado antes do sync.

### Sync executado (`pipelines/sync_region_daily.py --sync`)

**33.343 linhas** carregadas no Neon (primeira carga — sem tabela anterior, backup não aplicável). GMV total: R$ 50.035.397,78 (idêntico à fonte).

### Reconciliação pós-carga (Neon vs. Data Mart)

| Verificação | Resultado |
|---|---|
| Contagem total | 33.343 = 33.343 |
| Combinações marketplace × loja × mês | 75 = 75 (idênticas nos dois lados — zero linhas exclusivas de qualquer lado) |
| GMV, orders, units_sold, canceled_orders, returned_orders, uf_known/eligible, shipping_cost_covered/eligible por marketplace × loja × mês | Idênticos em todas as 75 combinações |
| Duplicidade na chave `(date, marketplace_id, loja_id, uf)` | 0 |
| Nulos obrigatórios | 0 |
| Numerador > denominador | 0 |
| TikTok (`marketplace_id=1`) | 0 linhas |
| Colunas de PII | 0 (20 colunas, nenhuma suspeita) |
| Barbours nov/2025–mar/2026 (`uf_fill_pct`) | 42,26% / 33,86% / 1,72% / 0,12% / 0,27% — **idêntico ao Data Mart (§10)**, baixa cobertura preservada, não mascarada |

Diagnóstico idempotente pós-carga (`pipelines/sync_region_daily.py --diagnose`): `Precisa sincronizar: False` — fonte e destino batem exatamente.

### Escopo não tocado

Frontend, endpoints `/regioes/*`, e qualquer deploy continuam intocados — nenhum arquivo de `apps/web` ou `apps/api/app/routers` foi alterado nesta etapa.

### Gate 6C

Bloqueado, aguardando autorização explícita separada: endpoints `/regioes/*` lendo `marts.fact_marketplace_region_daily`, e qualquer mudança de frontend.

### Gate 6C (parte 2) — refresh incremental de `gold.marketplace_region_daily` (2026-07-15)

Diagnóstico (Gerencial, Resumo Executivo, achado `stale_data` regional) confirmou que `gold.marketplace_region_daily` no Data Mart não avançava desde 2026-07-09. Causa raiz, por marketplace (não é uma causa única):

- **ML**: a fonte real do loader (`raw.ml_orders`) está fresca (dado até o dia corrente) — o gap era só porque `pipelines/ingestion/gold_regional/loader.py::execute_first_load()` nunca foi reexecutado. Essa função recalcula o histórico **inteiro** sem filtro de data e faz um INSERT simples (sem `TRUNCATE`) numa tabela com `UNIQUE (date, marketplace_id, loja_id, uf)` — rodá-la de novo hoje falharia por violação dessa constraint (rollback seguro, mas inútil). **Não é um comando de refresh**, é literalmente só a carga inicial do Gate 6A.3, e continua exatamente assim (código intocado).
- **Shopee**: a fonte real do loader (`silver.stg_shopee_order_item_snapshots`) está ela mesma parada em **2026-05-31** — `gold.marketplace_region_daily` já está 100% em dia com essa fonte para Shopee. O gap aqui é **upstream**, no transform raw→silver do Shopee (fora do escopo deste loader) — **não resolvido neste gate, propositalmente**.
- **TikTok**: nunca tem linha regional em nenhuma fonte mapeada (decisão de domínio, Gate 6A/6B) — nenhuma mudança aqui.

**Solução implementada**: `pipelines/ingestion/gold_regional/loader.py` ganhou um segundo caminho de carga, `execute_incremental_load()`, e um modo somente-leitura equivalente, `diagnose_incremental_load()`, expostos via CLI:

```bash
python -m pipelines.ingestion.gold_regional.loader --diagnose      # somente leitura, nunca abre conexao de escrita
python -m pipelines.ingestion.gold_regional.loader --incremental   # escreve; requer .env.gold-write.local (mesmo secret do Gate 6A)
```

Desenho da carga incremental:
- Para cada marketplace suportado (ML, Shopee), calcula `MAX(date)` já carregado em `gold.marketplace_region_daily` e só recalcula/insere linhas com `date` posterior a esse valor — nunca `TRUNCATE`/`DELETE`/`UPDATE`, só `INSERT` das linhas novas.
- Um marketplace sem novidade na fonte (hoje, Shopee) nunca bloqueia o refresh dos demais (hoje, ML) — cada marketplace é avaliado e carregado independentemente dentro da mesma transação.
- Se **nenhum** marketplace tiver data nova, a execução retorna `no_op` sem criar staging nem tentar inserir nada.
- Mesma disciplina transacional do `execute_first_load()`: 1 transação, advisory lock (`ADVISORY_LOCK_KEY` compartilhado — as duas funções nunca rodam concorrentemente entre si), validações recalculadas antes do insert (duplicidade, nulos, numerador≤denominador, reconciliação de GMV — escopadas à janela incremental, não à fonte inteira), validação pós-insert (zero linhas TikTok), rollback completo em qualquer falha, sem retry automático.
- `execute_first_load()` permanece **intocado** — usado só para uma eventual carga inicial nova (ex.: um ambiente do zero), nunca para refresh.

**Status após esta implementação**: código pronto e testado (42 testes em `pipelines/tests/test_gold_regional_loader.py`, incluindo o caminho incremental), mas **`--incremental` ainda não foi executado em produção nesta rodada** — só `--diagnose` (somente leitura). Scheduler segue **desativado** (Fase 3B, ver `docs/backlog.md` e memória de sessão) — a decisão de rodar `--incremental` manualmente e/ou de incorporar este comando a um pipeline recorrente fica para uma próxima rodada, com autorização explícita separada.

### Gate S1 — auditoria: por que Shopee precisa de refresh por janela, não de `--incremental` (2026-07-17)

Auditoria read-only (sem alteração de código) confirmou a causa raiz descrita no início desta seção: `--incremental` só insere `date > MAX(date)` já carregado por marketplace. Isso é seguro para ML (fonte não tem exports sobrepostos), mas **não serve para Shopee**: a automação externa de scraping (`docs/shopee_datamart_operacao_completa.md`) baixa exports de janela móvel, que podem trazer, no mesmo arquivo, a correção de um pedido de um dia **já carregado** na Gold. Como `gold.marketplace_region_daily` só recebe `INSERT` (nunca `UPDATE`/`DELETE`) e tem `UNIQUE (date, marketplace_id, loja_id, uf)`, essa correção nunca chegaria via `--incremental` — seria descartada pelo filtro de data, ou colidiria com a constraint se alguém tentasse forçar.

Auditado também: não existe hoje metadado (`file_id`/`batch_id`/`source_filename`/`ingested_at`) suficiente para inferir automaticamente **quais** `order_date` um novo arquivo afetou — um único export cobre uma janela ampla e pode corrigir um pedido de meses atrás. Decisão do Gate S1: exigir `--date-from`/`--date-to` explícitos no MVP, nunca inferência automática de janela.

Proposta de contrato (ainda não implementada neste gate): `--diagnose-shopee-window`/`--refresh-shopee-window`, com `DELETE` restrito por `marketplace_id=SHOPEE AND date BETWEEN`, backup pré-delete, secret dedicado novo (nunca reaproveitar `.env.gold-write.local` do `--incremental`, já que este seria o primeiro `DELETE` de todo o módulo), e sync para Neon permanecendo em passo manual separado via `sync_region_daily.py --sync` (que já faz refresh completo Data Mart→Neon, sem o problema de `MAX(date)`).

### Gate S2 — `--diagnose-shopee-window` (somente leitura) implementado (2026-07-17)

Implementado em `pipelines/ingestion/gold_regional/loader.py`: `diagnose_shopee_window(read_url, date_from, date_to)` + CLI:

```bash
python -m pipelines.ingestion.gold_regional.loader \
  --diagnose-shopee-window --date-from YYYY-MM-DD --date-to YYYY-MM-DD
```

**100% somente leitura** — mesma disciplina de `diagnose_incremental_load`: sessão `readonly=True`, nunca cria staging/temp table, nunca lê o secret de escrita (`.env.gold-write.local`, usado só por `--incremental`). Nesta versão original do Gate S2 a sessão usava `autocommit=True` (cada consulta no seu próprio snapshot) — **substituído no Gate S2.1** por uma única transação read-only `REPEATABLE READ` com `autocommit=False` (ver seção abaixo). **Não implementa `--refresh-shopee-window` nem qualquer `DELETE`/`INSERT`** — isso fica para o Gate S3.

Recalcula, a partir de `silver.stg_shopee_order_item_snapshots`, o **mesmo dedup "arquivo vencedor"** já usado pela carga (`DISTINCT ON (brand, order_id) ORDER BY file_id DESC` + join de volta preservando multi-item), escopado à janela pedida via bind parameter (`order_date BETWEEN %(date_from)s AND %(date_to)s` — nunca literal de string, porque aqui a data **é** entrada de usuário, diferente do `min_date` interno do incremental). Compara com o que já está em `gold.marketplace_region_daily` para Shopee na mesma janela e reporta: linhas/GMV/orders atuais vs. recalculados, `rows_to_delete`/`rows_to_insert` (impacto de um futuro refresh), delta de GMV/orders, se a janela sobrepõe dado já existente, alerta se a fonte recalculada zerar com a Gold tendo linhas, e duplicidade/nulos/numerador>denominador no recálculo.

Validações de janela (antes de qualquer conexão): `date_from <= date_to`, `date_to` não pode ser futuro, janela máxima de `MAX_SHOPEE_WINDOW_DAYS = 180` dias.

Validado com 27 testes novos (`pipelines/tests/test_gold_regional_window_diagnose.py`) + suíte completa (1092 testes) sem regressão, e com uma execução real read-only contra o Data Mart: janela `2026-05-25..2026-05-31` (já carregada) retornou recálculo **idêntico** à Gold (768 linhas, GMV R$ 1.234.373,97, delta zero) — confirma que a lógica de dedup recalculada bate exatamente com o que já foi validado na carga original.

**Status**: só o diagnose existe. `--refresh-shopee-window` (novo secret dedicado, `DELETE` restrito, backup, transação, rollback) é o Gate S3, ainda não implementado — autorização separada.

### Gate S2.1 — endurecimento do diagnose: snapshot consistente + comparação exata por chave (2026-07-17)

Micro-gate de endurecimento **antes** de qualquer `DELETE` do Gate S3. Continua **100% somente leitura, sem escrita, sem DDL/DML, sem secret novo**. Dois problemas do Gate S2 corrigidos:

**1. Snapshot consistente.** No Gate S2, `diagnose_shopee_window` rodava em `autocommit=True` com cada `SELECT` num snapshot próprio — uma ingestão concorrente entre consultas poderia fazer a Gold e a fonte serem lidas em instantes diferentes. Agora todas as consultas rodam na **mesma conexão, numa única transação read-only `REPEATABLE READ`** (`autocommit=False`): o snapshot é fixado no primeiro comando e mantido para todas as demais. Rollback explícito ao final **inclusive no sucesso** (transação só de leitura — nada a commitar; o rollback só fecha o snapshot limpo); rollback também em qualquer exceção; `close` garantido em `finally`. `readonly=True` mantém a garantia de que nem um bug conseguiria escrever.

**2. Comparação exata por chave (não só agregados).** O Gate S2 comparava apenas linhas/GMV/orders totais — cego a **redistribuição entre chaves** (ex.: pedidos migrando de `uf='XX'` para `uf='SP'` sem alterar nenhum total). Agora um `FULL OUTER JOIN` no grão `(date, marketplace_id, loja_id, uf)` compara a Gold Shopee atual com o recálculo da fonte, campo a campo com `IS DISTINCT FROM` (trata `NULL` vs. `0` corretamente, o que `<>` não faz) em todas as 13 colunas de negócio (`gmv`, `orders`, `units_sold`, `canceled_orders`, `returned_orders`, `seller_shipping_cost`, `buyer_shipping_fee`, `estimated_shipping_fee`, `reverse_shipping_fee`, `uf_known_orders`, `uf_eligible_orders`, `shipping_cost_covered_orders`, `shipping_cost_eligible_orders`) — `id`/`ingested_at`/`source_updated_at` são excluídos de propósito (o diagnose responde "os DADOS mudariam", não "a linha foi reingerida"). Sem soma/hash aproximado como substituto da comparação campo a campo.

O relatório passou a expor `gold_only_key_count`, `source_only_key_count`, `changed_key_count`, e duas propriedades derivadas:
- `would_change_data` = `gold_only > 0 OR source_only > 0 OR changed > 0`. **`False` não é erro** — significa janela já reconciliada (Gold e fonte batem chave a chave e campo a campo).
- `structurally_safe_for_refresh` = `not zero_source_risk AND duplicate_key_count == 0 AND null_required_count == 0 AND numerator_over_denominator_count == 0`. Independe de `would_change_data` (as duas dimensões são ortogonais).

**Exit codes da CLI**: `0` OK (reconciliada ou não, desde que estruturalmente sã); `2` config/janela inválida; `3` falha de consulta; `4` **estruturalmente insegura** (`structurally_safe_for_refresh=False`) — o Gate S3 nunca deve prosseguir com a janela sem investigação. `would_change_data=False` nunca gera exit não-zero.

Validado: **50 testes** em `pipelines/tests/test_gold_regional_window_diagnose.py` (inclui garantias transacionais — `readonly`/`REPEATABLE READ`/`autocommit=False`/mesma conexão/rollback no sucesso e na falha/close sempre; redistribuição de UF com totais iguais; `NULL` vs `0` via `IS DISTINCT FROM`; bloqueios estruturais; exit codes) + suíte completa (1115 testes) sem regressão. Smoke real read-only: janelas `2026-05-25..05-31` (768 linhas) e `2026-06-01..06-30` (186 linhas) retornaram `would_change_data=False`/`structurally_safe_for_refresh=True`/exit 0 — Gold e fonte idênticas chave a chave e campo a campo, confirmando que a comparação exata bate com o que a carga original produziu. Nenhum `order_id`/CPF/filename/URL/host/linha individual impresso.

**Status**: diagnose endurecido e confiável para decidir se uma janela precisa ser substituída. `--refresh-shopee-window` (escrita real) segue **inexistente** — Gate S3, autorização separada.

### Gate S3 — refresh e restore transacionais por janela (implementado, NÃO executado — 2026-07-17)

**Primeiro caminho de escrita da Gold regional que faz `DELETE`** (todos os outros — `execute_first_load`, `execute_incremental_load`, DDL — só fazem `INSERT`/DDL). Implementado, testado com fakes, **nenhuma execução real** nesta rodada (sem conexão a banco, sem DDL/DML remoto, sem criação de secret/role).

**Escopo do DELETE/INSERT — sempre e só:**
```sql
marketplace_id = SHOPEE_MARKETPLACE_ID AND date BETWEEN date_from AND date_to
```
Nenhuma linha ML, TikTok ou Shopee fora da janela pode ser alterada — garantido pelo `WHERE` explícito **e** por um fingerprint agregado de "tudo fora do escopo" (`NOT (marketplace_id=SHOPEE AND date BETWEEN...)`, count+13 somas) conferido sob o mesmo lock antes e depois de qualquer escrita (defesa em profundidade, não só confiança no `WHERE`).

**Secret e privilégio mínimo** — novo módulo `pipelines/ingestion/gold_regional/window_write_conn.py`, secret dedicado `.env.gold-window-write.local` (2 chaves: `DATAMART_GOLD_WINDOW_WRITE_URL`, `I_UNDERSTAND_THIS_DELETES_GOLD_SHOPEE_WINDOW`) — **nunca** `.env.gold-write.local` (o secret do `--incremental`, que só autoriza `INSERT`). Preflight dedicado, somente leitura, com uma regra central desde o Gate S3.1 (ver abaixo): **"não foi possível confirmar" nunca equivale a aprovado** — qualquer checagem inconclusiva (`None`) bloqueia. Exige: conexão autenticada, `pg_is_in_recovery()=false`, `system_identifier` disponível nos dois lados e EXATAMENTE igual (sem fallback por database+porta), `rolsuper`/`rolcreatedb`/`rolcreaterole`/`rolreplication`/`rolbypassrls` todos `false`, membro de `rds_superuser` CONFIRMADO `false` (bloqueia, não é mais aviso), SSL CONFIRMADO em uso, `gold.marketplace_region_daily` e sua sequence existem, `USAGE` em `silver`/`gold`, `SELECT` em `silver.stg_shopee_order_item_snapshots`, `SELECT`/`INSERT`/`DELETE` em `gold.marketplace_region_daily`, `USAGE` na sequence do `id` (BIGSERIAL), `TEMP` no database (staging). Também bloqueia se a credencial TIVER privilégios proibidos: `CREATE` no schema `gold`, `UPDATE` ou `TRUNCATE` em `gold.marketplace_region_daily` (least-privilege violado). Nunca exige nem concede nada em tabelas ML/TikTok.

**`--refresh-shopee-window --date-from --date-to --audit-path [--confirm-empty-window]`** (`execute_shopee_window_refresh`): 1 conexão de escrita, `autocommit=False` → advisory lock (**MESMA** chave de `execute_first_load`/`execute_incremental_load` — nunca rodam concorrentemente) → `LOCK TABLE gold.marketplace_region_daily IN SHARE ROW EXCLUSIVE MODE` → fingerprint fora do escopo (antes) → staging `TEMP` materializado **uma única vez** (mesmo dedup arquivo-vencedor do Gate S2) → validações do staging (duplicidade, nulos, numerador≤denominador, **NaN/negativo explícito** — `'NaN'::numeric >= 0` é `TRUE` no Postgres, achado já documentado do projeto —, escopo marketplace/janela) → `structurally_safe_for_refresh` **recalculado sob o lock** (nunca reaproveita um `diagnose` anterior) → comparação por chave Gold-atual vs. staging decide `no_op` → **backup atômico publicado ANTES do `DELETE`** → `DELETE` escopado (`rowcount == contagem anterior`) → `INSERT` do staging (`SQL_INSERT_FINAL`, reaproveitado do Gate S2 — colunas explícitas, nunca `id`, `rowcount == contagem do staging`) → reconciliação pós-insert (chave a chave e campo a campo) → fingerprint fora do escopo (depois, deve bater com antes) → `COMMIT`. `ROLLBACK` completo em qualquer falha, sem retry. Se o `COMMIT` já ocorreu e só a liberação do lock/`close` falhar, o resultado `committed` é preservado (aviso sanitizado, sem sugestão de retry).

**Backup atômico** — mesmo padrão já auditado em `pipelines/ingestion/shopee_raw/backfill_ads_metadata.py`, **reimplementado** (não importado, para não acoplar os dois pacotes de ingestão): `tempfile.mkstemp` exclusivo no mesmo diretório → `flush`+`os.fsync` → `os.link` (nunca `os.replace`/`os.rename` — falha com `FileExistsError` se o destino já existir, nunca sobrescreve) → temporário sempre removido → relê o arquivo publicado, revalida a estrutura completa, só então calcula e publica o `.sha256` companion pelo mesmo mecanismo. JSON versionado (`schema_version=1`) com `created_at_utc`, `marketplace_id`, `date_from`/`date_to`, `grain_key`, `business_columns`, contagens/agregados before/after, e `before_records`/`planned_after_records` — só linhas do grão regional (`date, marketplace_id, loja_id, uf` + 13 campos de negócio), **nunca** `order_id`/CPF/filename/`file_id`/URL/host/credencial (a Gold regional não tem essas colunas). Decimal sempre como string; datas ISO-8601.

**`--restore-shopee-window --audit-path --expected-backup-sha256`** (`execute_shopee_window_restore`, compare-and-swap): backup tratado como **entrada não confiável** — hash recalculado e comparado **antes de qualquer conexão**, JSON/estrutura validados por completo (`validate_window_backup_payload` — `schema_version`, marketplace só Shopee, janela válida ≤180 dias, registros campo a campo type-safe, chave única). Sob o mesmo advisory lock + table lock: compara o estado **atual** da Gold na janela do backup contra `planned_after_records` — se não for **exatamente** igual, aborta antes de qualquer `DELETE` (compare-and-swap — impede restaurar sobre uma carga posterior que já mudou a janela). `DELETE` restrito → insere `before_records` (1 `INSERT` por linha, nunca bulk — `execute_values` paginado tornaria `rowcount` não confiável entre páginas, gotcha conhecido do psycopg2) → reconcilia exatamente contra `before_records` → confirma fingerprint fora do escopo inalterado → `COMMIT`.

**Resultado/exit codes**: `ShopeeWindowRefreshResult.outcome` ∈ `committed|no_op|blocked|failed`; `ShopeeWindowRestoreResult.outcome` ∈ `committed|blocked|failed`. CLI: `0` committed/no_op; `2` config/janela/audit_path/secret/preflight inválido (nunca abriu a transação real); `3` blocked (bloqueio estrutural ou CAS recusado sob o lock); `4` failed (rollback completo executado).

**Validado**: 32 testes (`test_gold_regional_window_write_conn.py`) + 30 (`test_gold_regional_window_refresh.py`) + 33 (`test_gold_regional_window_restore.py`) — todos com fakes/`tmp_path`, nenhum banco real — cobrindo ordem transacional completa, cada bloqueio isolado, `NaN`/negativo, `--confirm-empty-window` (só libera esse caso, não desativa as demais validações), corrida na publicação do backup (destino nunca sobrescrito), rowcount divergente em `DELETE`/`INSERT`, reconciliação pós-operação divergente, fingerprint fora do escopo alterado, CAS do restore recusando sem tocar o banco, e o teste de regressão que passou a reconhecer `SQL_REFRESH_DELETE` como a **única** exceção sancionada de todo o módulo (nenhuma outra constante `SQL_*` pode conter `DELETE`/`UPDATE`/`TRUNCATE`/`DROP`). Suíte completa: **1213 testes**, sem regressão em `execute_first_load`/`execute_incremental_load`/`--diagnose`/`--incremental`/Gate S2/S2.1.

**Status**: implementado e testado, **zero execução real** — sem `.env.gold-window-write.local` real, sem conexão a banco, sem DDL/DML remoto, sem sync Neon, sem alteração de scheduler. A automação externa de scraping **não deve habilitar** `--refresh-shopee-window` até o piloto do Gate S4 (permissão/credencial dedicada e restrita para a pessoa responsável pelo scraping, autorização explícita separada). Sync Neon continua em passo manual separado via `sync_region_daily.py --sync`, só depois de um refresh reconciliado — não foi tocado nesta rodada.

### Gate S3.1 — hardening de conexão, preflight e backup (revisão de segurança pré-commit, 2026-07-18)

Revisão de segurança encontrou bloqueios genuínos no Gate S3 antes de qualquer piloto real. **Nenhuma execução real nesta rodada também** — só código, testes e esta documentação.

**1. Ciclo de conexão corrigido.** `psycopg2.connect()` estava FORA de qualquer `try/except` em `execute_shopee_window_refresh`/`execute_shopee_window_restore` — uma falha de conexão propagaria como exceção nativa não sanitizada (risco real de vazar host/porta/usuário, o mesmo tipo de vazamento já documentado historicamente em `write_conn.py`). Corrigido: `connect()` protegido (falha vira `outcome="failed"` sanitizado), `conn.autocommit = False` movido para DENTRO do `try` (se a própria atribuição falhar, o `finally` ainda fecha a conexão), rollback em todo ponto de abort (não só no `except` genérico) passou a ser **best-effort** via `_rollback_best_effort` — nunca deixa uma falha do rollback mascarar o motivo original do abort. `backup_path`/`backup_sha256` agora são preservados no resultado mesmo quando a falha é uma exceção genérica pós-backup (DELETE/INSERT/commit levantando algo além de um rowcount divergente) — antes só os caminhos de rowcount divergente preservavam essa informação. `commit()` continua estruturalmente incapaz de produzir `outcome="committed"` se levantar (o resultado só é montado DEPOIS que `commit()` retorna sem exceção) — agora com teste explícito provando isso.

**2. Ordem de validação corrigida na CLI.** `run_refresh_shopee_window_cli`/`run_restore_shopee_window_cli` liam o secret e rodavam o preflight de escrita **antes** de validar janela/`audit_path`/hash/JSON — uma janela invertida ou um `audit_path` relativo abriam uma conexão de banco real e liam um arquivo de disco sensível desnecessariamente. Corrigido: `DATAMART_DATABASE_URL` → janela (`_validate_shopee_window`) → `audit_path` (refresh: `_validate_new_window_audit_path`; restore: `_validate_and_load_window_backup` — caminho, formato do SHA, TAMANHO do arquivo antes de `read_text`/`json.loads`, SHA recalculado, JSON parseado, estrutura completa) — só DEPOIS disso o secret é lido e o preflight roda. A função de execução (`execute_shopee_window_refresh`/`execute_shopee_window_restore`) sempre REVALIDA tudo do zero — a checagem antecipada da CLI é só fail-fast, nunca substitui a autoritativa (o arquivo pode mudar entre as duas chamadas).

**3. Preflight realmente least-privilege.** Três lacunas corrigidas em `window_write_conn.run_window_preflight`: (a) o fallback para comparação por database+porta quando `system_identifier` está indisponível foi **removido** — aceitável no preflight genérico do `--incremental`, não aceitável num caminho com `DELETE`, onde "não confirmado" deve bloquear, nunca aprovar por um substituto mais fraco; (b) `rolcreatedb`/`rolcreaterole`/`rolreplication`/`rolbypassrls` passaram a ser checados e bloqueiam se `true` (antes só `rolsuper` era checado); (c) membro de `rds_superuser` **passou de aviso para bloqueio** — só a mensagem nativa "does not exist" (papel realmente não existe, não é RDS) é aceita como confirmação válida de não-membro, qualquer outra falha na consulta bloqueia por ser inconclusiva; (d) SSL não confirmado (`false` ou `None`) agora bloqueia, não é mais só aviso; (e) `CREATE` no schema `gold` e `UPDATE`/`TRUNCATE` em `gold.marketplace_region_daily` passaram a ser verificados como **privilégios proibidos** — se a credencial os tiver, é sinal de que não foi escopada como least-privilege, e o preflight bloqueia até ser corrigida.

**4. Backup validado integralmente, não só o SHA.** "O SHA comprova integridade dos bytes, mas não torna metadados semanticamente verdadeiros" — `validate_window_backup_payload` ganhou checagens que faltavam: `created_at_utc` como timestamp UTC válido; `grain_key`/`business_columns` comparados EXATAMENTE (conjunto e ordem) contra as constantes oficiais; `before_count`/`after_count` (tipo, `bool` recusado, `>=0`, e — crucialmente — IGUAIS ao tamanho real das respectivas listas, o que antes nunca era conferido); `before_aggregates`/`after_aggregates` com GMV/pedidos RECALCULADOS a partir dos registros e comparados byte a byte contra o declarado (antes esses campos existiam no JSON mas nunca eram inspecionados). Limite de `MAX_WINDOW_BACKUP_RECORDS` (180 dias × 5 lojas × 28 UFs = 25.200) e `MAX_WINDOW_BACKUP_FILE_BYTES` (64 MiB, verificado via `stat()` **antes** de `read_text`/`json.loads`).

**5. Nenhum caminho local exposto.** Mensagens de validação (`_validate_new_window_audit_path`) e saída da CLI passaram a usar só categoria/`basename` — nunca o caminho absoluto (que em Windows inclui o nome de usuário) nem `repo_root`. A confirmação de sucesso do refresh mostra só `audit_path.name` + SHA-256.

**Validado**: 45 testes novos/reescritos (13 em `test_gold_regional_window_write_conn.py`, 12 em `test_gold_regional_window_refresh.py`, 20 em `test_gold_regional_window_restore.py`) cobrindo: `PoisonConn` provando que janela/`audit_path`/hash/JSON inválidos nunca leem o secret nem abrem conexão (nem de preflight); `commit()` falhando nunca produz `committed`; `connect()`/atribuição de `autocommit` falhando fecha a conexão; publicação do `.sha256` falhando após o JSON já publicado (artefato parcial preservado, nunca removido automaticamente, mensagem sanitizada); `DELETE`/`INSERT` levantando exceção genérica preserva `backup_path`/SHA no resultado; todas as novas regras bloqueantes do preflight (`rolcreatedb`/`rolcreaterole`/`rolreplication`/`rolbypassrls`, `rds_superuser` confirmado vs. desconhecido, SSL, `CREATE`/`UPDATE`/`TRUNCATE` proibidos, sequence ausente, `system_identifier` ausente nunca cai para database+porta); validações adversariais do backup (`created_at_utc` malformado, `grain_key`/`business_columns` errados, contagens divergentes, agregados recalculados divergentes, limite de registros excedido, arquivo excedendo o tamanho máximo); caminho fictício contendo nome de usuário nunca aparece na saída da CLI. Suíte completa: **1258 testes**, sem regressão em `execute_first_load`/`execute_incremental_load`/`--diagnose`/`--incremental`/Gate S2/S2.1.

**Status**: hardening implementado e testado, **zero execução real** nesta rodada — sem secret/role real, sem conexão a banco, sem sync Neon. Ainda **nenhum piloto real ocorreu** contra o Data Mart — a próxima etapa é a revisão final pré-commit, não uma execução.

### Gate S3.2 — correção final dos findings da revisão pré-commit (2026-07-18)

Quatro lacunas defensivas fechadas, sem alterar SQL/dedup/CAS/escopo do DELETE — **nenhuma execução real nesta rodada também**:

1. **Preflight nunca levanta**: o bloco de consultas de `run_window_preflight` (roles, privilégios, tabela, sequence, SSL, versão) agora está sob `try/except` — qualquer falha inesperada vira `blocking_reason` sanitizado, todos os campos do `safe_summary` são inicializados como `None` (inconclusivo) antes das consultas (nenhuma variável indefinida), a conexão fecha em qualquer caminho, e a função SEMPRE retorna um report. As duas CLIs ainda ganharam uma barreira defensiva em volta do preflight (exit 2 sanitizado se uma regressão futura o fizer levantar).
2. **"Inconclusivo bloqueia" literal**: toda condição sensível usa comparação de identidade explícita (`is True`/`is not False`/`is not True`) — `pg_is_in_recovery`, os 5 atributos de role, `rds_superuser`, SSL, privilégios obrigatórios E proibidos (`CREATE`/`UPDATE`/`TRUNCATE`, que antes usavam truthiness e aprovariam `None` silenciosamente). Valores truthy não-booleanos (ex.: `1`/`0`) também bloqueiam.
3. **Leitura atômica do backup**: `_validate_and_load_window_backup` abre o arquivo UMA vez em binário — `os.fstat` no descritor aberto (teto de tamanho), leitura limitada a `MAX+1` bytes (bloqueia se `fstat` mentir e vier byte excedente), SHA-256, decodificação UTF-8 (`UnicodeDecodeError` agora capturado) e parse JSON, tudo sobre o MESMO conjunto de bytes — fecha a janela TOCTOU do desenho anterior (stat → reabrir p/ hash → reabrir p/ read_text).
4. **Validadores de caminho nunca levantam**: `_validate_new_window_audit_path`/`_validate_existing_window_audit_path` capturam `OSError`/`RuntimeError`/`ValueError` de `resolve()`/`is_dir()`/`exists()`/`is_file()` em paths patológicos e retornam problema sanitizado (sem caminho absoluto), nunca traceback.

Validado: 37 testes novos/reescritos (parametrizado de "None bloqueia individualmente" com 18 casos, exceção em consulta intermediária com 6 pontos de falha, barreira das duas CLIs, UTF-8 inválido, `fstat` mentindo, leitura única provada com `read_text`/`_sha256_file` boomados, OSError/RuntimeError em resolve/exists). Suíte completa: **1295 testes**, sem regressão.

**Gate S3.3 (2026-07-18)**: fechado o último finding herdado — nos helpers compartilhados de `write_conn.py` (`_connect_readonly`/`_fetch_target_identity`, usados pelo preflight de janela), um `set_session()` que falhasse após o `connect()` vazava a conexão; agora o `set_session()` roda dentro do ciclo protegido, a conexão fecha em best-effort em qualquer falha (sem nunca mascarar a exceção original), o contrato de retorno/fallback do helper genérico permanece idêntico, e a regra mais estrita do preflight de janela (que recusa o fallback) permanece intacta. 9 testes novos (8 de ciclo de vida em `test_gold_regional_write_conn.py` + 1 regressão no preflight de janela); suíte completa: **1304 testes**. Zero execução real, como nas rodadas anteriores.

### Gate S4.1 — preparação operacional read-only do piloto (2026-07-20)

Preparação do piloto real de `--refresh-shopee-window`, **100% somente leitura**: nenhuma escrita, nenhum secret/role criado, nenhum GRANT/REVOKE/DDL executado.

**Smoke real do diagnose contra o Data Mart.** Janela de controle `2026-01-01..2026-01-07`: 512 linhas, reconciliada (`would_change_data=False`, `structurally_safe_for_refresh=True`, exit 0). Junho/2026 inteiro (até o `MAX(date)` real da fonte, `2026-06-25` — confirmado via `--diagnose` geral) dividido em blocos de até 7 dias — `06-01..06-07` (0 linhas dos dois lados), `06-08..06-14` (0 linhas dos dois lados), `06-15..06-21` (80 linhas), `06-22..06-25` (106 linhas): **todos** vieram `would_change_data=False`/`structurally_safe_for_refresh=True`/exit 0, isto é, Gold e fonte já batem chave a chave e campo a campo em toda a janela testada. Nenhum `order_id`/CPF/filename/URL/host/linha individual impresso em nenhuma consulta.

**Candidato ao piloto: nenhum encontrado.** Nenhuma das janelas testadas satisfaz simultaneamente `would_change_data=True` e `structurally_safe_for_refresh=True` — todas já estão reconciliadas. Isso é consistente com o Gate S2.1 (smoke anterior de 2026-07-17 já via `2026-06-01..06-30` reconciliado) e com o "piloto real" registrado em `docs/shopee_datamart_operacao_completa.md` (2026-07-17): o lote inserido na automação externa aparentemente não alterou o resultado do dedup "arquivo vencedor" para nenhum `(brand, order_id)` já coberto — não há, portanto, uma janela real disponível hoje para validar `--refresh-shopee-window` fazendo diferença observável. **Não é falha de código** — é o resultado esperado quando a fonte e a Gold já convergem. Se uma nova janela candidata aparecer no futuro (novo export cobrindo um período ainda não reconciliado), repetir o diagnose antes de escolher.

**Auditoria read-only de objetos/permissões** (mesma URL de leitura `DATAMART_DATABASE_URL`, sessão `readonly=True`, reaproveitando `write_conn._connect_readonly`): schemas `silver` e `gold` existem; `silver.stg_shopee_order_item_snapshots` existe; tabela `gold.marketplace_region_daily` e sua sequence `gold.marketplace_region_daily_id_seq` existem, ambas com owner `postgres` (role técnica do cluster, não uma conta pessoal); a role `gold_shopee_window_writer` **ainda não existe**. ACLs atuais nos dois objetos envolvem só roles técnicas já documentadas/esperadas neste Data Mart (owner, roles de infraestrutura de leitura/replicação, e as duas roles já documentadas da automação Shopee — leitura e escrita Raw/Silver); nenhuma delas tem hoje `DELETE` em `gold.marketplace_region_daily` fora do owner.

**SQL de provisionamento — proposto, NÃO executado** (privilégio mínimo; role criada `NOLOGIN`, sem senha nenhuma no script — a senha é um passo operacional separado, nunca literal em arquivo versionado):

```sql
-- Executar como owner/superuser do banco (postgres).
-- Passo 1: criar a role SEM capacidade de login e SEM senha.
CREATE ROLE gold_shopee_window_writer
    NOLOGIN
    NOSUPERUSER
    NOCREATEDB
    NOCREATEROLE
    NOREPLICATION
    NOBYPASSRLS;

GRANT CONNECT ON DATABASE datamart TO gold_shopee_window_writer;
GRANT TEMP ON DATABASE datamart TO gold_shopee_window_writer;

GRANT USAGE ON SCHEMA silver TO gold_shopee_window_writer;
GRANT USAGE ON SCHEMA gold TO gold_shopee_window_writer;

GRANT SELECT ON silver.stg_shopee_order_item_snapshots TO gold_shopee_window_writer;
GRANT SELECT, INSERT, DELETE ON gold.marketplace_region_daily TO gold_shopee_window_writer;
GRANT USAGE ON SEQUENCE gold.marketplace_region_daily_id_seq TO gold_shopee_window_writer;

-- Deliberadamente NUNCA concedidos (o preflight de `window_write_conn.py`
-- bloqueia se algum destes estiver presente): CREATE no schema gold;
-- UPDATE/TRUNCATE em gold.marketplace_region_daily; qualquer privilégio em
-- tabelas ML/TikTok; rds_superuser; ownership da tabela/schema.
```

**Passo 2 — operacional, fora do script versionado (não executado, não commitado):**

```
\password gold_shopee_window_writer
ALTER ROLE gold_shopee_window_writer LOGIN;
```

- `\password` roda interativamente no `psql` e nunca deixa a senha em texto no SQL, no histórico do shell ou em qualquer arquivo versionado — é o próprio cliente que troca a senha via mensagem de protocolo, não uma linha de comando com o valor literal.
- Se o processo for interrompido entre o `CREATE ROLE` e o `ALTER ROLE ... LOGIN`, a role fica `NOLOGIN` — inutilizável para autenticar, sem exigir rollback.
- Só rodar `ALTER ROLE ... LOGIN` depois que a senha já tiver sido definida por `\password` — nunca antes, para não abrir uma janela onde a role tem `LOGIN` habilitado sem senha própria.
- A senha nunca deve ser passada por argumento de linha de comando, colada em chat, incluída em commit, ou escrita em qualquer arquivo de documentação (nem mesmo como exemplo/placeholder).

**Modelo recomendado**: uma única role dedicada (como acima), não uma role `NOLOGIN` de privilégios + login separado. Motivo: a automação é um único processo controlado por uma pessoa responsável (o piloto do scraping Shopee), o mesmo padrão já usado para `shopee_datamart_reader`/`shopee_raw_silver_writer`, e revogação/rotação continuam simples (`ALTER ROLE ... NOLOGIN` ou troca de senha via `\password`) sem a complexidade adicional de duas roles para um caso de uso de um único operador.

**Contrato do secret** (arquivo ainda não criado, fora do Git, coberto por `.gitignore` via `.env.*`):

```
DATAMART_GOLD_WINDOW_WRITE_URL=<URL da credencial gold_shopee_window_writer>
I_UNDERSTAND_THIS_DELETES_GOLD_SHOPEE_WINDOW=1
```

Diferente de `.env.gold-write.local` (secret do `--incremental`, só `INSERT`) e de `DATAMART_DATABASE_URL` (leitura) — `window_write_conn.validate_window_write_guardrails` já bloqueia se as duas URLs coincidirem.

**Plano exato S4.2/S4.3 (não executado nesta rodada):**

- **S4.2** — criar a role/login (SQL acima, por quem tiver privilégio de `CREATEROLE`); criar `.env.gold-window-write.local` local (nunca commitado); rodar **somente** `window_write_conn.run_window_preflight` contra a credencial nova; ajustar GRANTs até o preflight aprovar (`ok=True`, sem `blocking_reasons`); nenhuma escrita em `gold.marketplace_region_daily` nesta etapa.
- **S4.3** — só depois do preflight aprovado: repetir `--diagnose-shopee-window` imediatamente antes (snapshot fresco); escolher/confirmar a menor janela candidata (`would_change_data=True` e `structurally_safe_for_refresh=True` — hoje nenhuma existe, ver acima); `--audit-path` novo, absoluto, fora do repo; **uma única** execução de `--refresh-shopee-window` (sem retry); validar o backup JSON publicado (SHA-256 + agregados recalculados batendo); reconciliar Gold vs. Silver na janela; confirmar ML/TikTok e Shopee fora da janela inalterados (fingerprint fora do escopo); rodar `--diagnose-shopee-window` pós-refresh e esperar `would_change_data=False`; parar **antes** do sync Neon (`sync_region_daily.py --sync` continua manual, gate separado).

**Status**: zero escrita, zero secret real criado, zero role criada, zero GRANT/REVOKE/DDL executado nesta rodada. `.env.gold-window-write.local` continua inexistente. Nenhuma janela candidata disponível hoje para o piloto real do S4.3 — próximo passo depende de uma nova janela Shopee ainda não reconciliada aparecer na fonte, ou de decisão explícita do Mário sobre como prosseguir sem candidato.

### Gate S4.2 — provisionamento da credencial dedicada + preflight real (2026-07-20)

**Autorização explícita do Mário**: criar a role, aplicar os privilégios mínimos documentados, criar o secret local gitignored e executar somente o preflight read-only — sem refresh, restore ou sync.

**Role criada**: `gold_shopee_window_writer`. Sequência aplicada exatamente como planejado no Gate S4.1: `CREATE ROLE ... NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS` (sem cláusula `PASSWORD`) → senha definida em seguida por comando parametrizado do driver (nunca interpolada em SQL, nunca logada) → GRANTs mínimos aplicados (`CONNECT`/`TEMP` no database, `USAGE` em `silver`/`gold`, `SELECT` em `silver.stg_shopee_order_item_snapshots`, `SELECT`/`INSERT`/`DELETE` em `gold.marketplace_region_daily`, `USAGE` na sequence) → **todas as verificações rodaram na mesma transação, antes do `COMMIT`** (mesmo padrão do resto do módulo: só persiste se as checagens baterem). `ALTER ROLE ... LOGIN` só rodou depois do secret local criado e validado — nunca antes.

**Conexão administrativa usada**: `.env.gold-write.local` (mesmo secret já existente do `--incremental`), usada exclusivamente para o provisionamento (`CREATE ROLE`/`GRANT`/`ALTER ROLE`) — nunca para escrever em tabela de dados. Precheck read-only confirmou antes de qualquer DDL: mesmo cluster físico da leitura (`system_identifier`), `pg_is_in_recovery=false`, SSL ativo, credencial com `CREATEROLE` e `GRANT OPTION` nos objetos alvo, `gold_shopee_window_writer` ainda não existia.

**Verificações pós-GRANT (todas `True` antes do commit)**: `rolcanlogin=false` (fase 1, antes do LOGIN) → `true` (fase 6, depois); `rolsuper`/`rolcreatedb`/`rolcreaterole`/`rolreplication`/`rolbypassrls` = `false`; `CONNECT`/`TEMP` no database = `true`; `USAGE` em `silver`/`gold` = `true`; `CREATE` em `gold` = `false`; `SELECT` na Silver = `true`; `SELECT`/`INSERT`/`DELETE` na Gold = `true`; `UPDATE`/`TRUNCATE` na Gold = `false`; `USAGE` na sequence = `true`; membro de `rds_superuser` = `false`; a role não é dona de nenhum objeto (`owns_nothing=true`); nenhum privilégio em qualquer outra tabela além dos 4 GRANTs esperados (checado via `aclexplode(pg_class.relacl)` — cobre qualquer schema, inclusive ML/TikTok, não só `silver`/`gold`).

**Secret local**: `.env.gold-window-write.local` criado de forma exclusiva/atômica (`open(..., "x")` — nunca sobrescreve), com exatamente as 2 chaves esperadas (`DATAMART_GOLD_WINDOW_WRITE_URL`, `I_UNDERSTAND_THIS_DELETES_GOLD_SHOPEE_WINDOW=1`), URL apontando para o cluster primary (mesmo `system_identifier` da leitura) com a nova role/senha, `sslmode=require` forçado. Validado com as funções já existentes (`load_window_write_secret`/`validate_window_write_guardrails`, sem nunca imprimir o conteúdo). Confirmado `git check-ignore` (coberto por `.env.*`) e ausente do `git status`.

**Preflight real — `window_write_conn.run_window_preflight()`** (só essa função; nenhuma CLI de refresh, nenhuma função `execute_*` foi chamada): `report.ok=True`, `blocking_reasons=[]`. Todos os checks bateram com o esperado: `pg_is_in_recovery=false`, `target_confirmado=true` (via `system_identifier`), `rolsuper`/`rolcreatedb`/`rolcreaterole`/`rolreplication`/`rolbypassrls`=`false`, `membro_rds_superuser=false`, `ssl_in_use=true`, tabela e sequence existem, `USAGE`/`SELECT`/`INSERT`/`DELETE`/`TEMP` obrigatórios=`true`, `gold_create=false`, `can_update_gold=false`, `can_truncate_gold=false`.

**Validação pós-provisionamento**: repetido o `--diagnose-shopee-window` na janela de controle (`2026-01-01..01-07`) — resultado **idêntico** ao do Gate S4.1 (512 linhas, GMV/orders inalterados, `would_change_data=False`), confirmando que o provisionamento (DDL/GRANT apenas) não tocou em nenhuma linha de dado.

**Status**: role criada e com `LOGIN` habilitado, privilégios mínimos aplicados e verificados, secret local criado/validado/gitignored. **Zero escrita em tabela de dados, zero backup, zero refresh, zero restore, zero sync Neon, zero pipeline/scheduler/deploy executado.** S4.3 continua bloqueado por ausência de janela candidata (ver Gate S4.1) e exige autorização explícita separada — não iniciado nesta rodada.

### Gate S4.3a — modo de validação do caminho de escrita, sem persistência (2026-07-20)

**Motivação**: esperar um `NO_OP` do `--refresh-shopee-window` real não é uma forma segura de validar o caminho de escrita — se a fonte mudar entre um diagnose e a execução real, o comando legitimamente avança para backup + `DELETE` + `INSERT`. Faltava um modo que exercitasse o MESMO caminho (secret dedicado, preflight, advisory lock, table lock, staging TEMP, validações estruturais, reconciliação Gold × fonte) sem nenhum risco de persistir dado, mesmo quando a janela está divergente.

**Implementado, NÃO executado contra banco real nesta rodada**: `validate_shopee_window_write_path(write_url, date_from, date_to)` em `pipelines/ingestion/gold_regional/loader.py` + CLI `--validate-shopee-window-write-path --date-from --date-to` (não exige `--audit-path` — nenhum backup é produzido).

**Por que é estruturalmente impossível escrever**: é uma função SEPARADA de `execute_shopee_window_refresh` — não uma variação com flag `dry_run`/`validate_only` que um default invertido pudesse liberar por engano. Ela literalmente não referencia (nem a função, nem sua CLI) nenhum dos símbolos que fariam uma escrita real: `SQL_REFRESH_DELETE`, `SQL_INSERT_FINAL`, `SQL_RESTORE_INSERT_ROW`, `_write_window_backup_atomic`, `execute_shopee_window_refresh`, `execute_shopee_window_restore`, ou qualquer chamada a commit — confirmado por um teste de regressão estática que inspeciona `inspect.getsource()` das duas funções. O único `INSERT` que existe no seu caminho é o da própria `stg_marketplace_region_daily` (`TEMP TABLE ... ON COMMIT DROP`, nunca sobrevive à transação). A transação **sempre** termina em `ROLLBACK` — inclusive no caminho de sucesso, inclusive quando a janela está divergente (`would_change_data=True`): a função só REPORTA a divergência e segue direto para o rollback, nunca avança para backup/refresh.

**Ordem**: validar janela (antes de conectar) → connect protegido → `autocommit=False` → MESMO advisory lock de `execute_first_load`/`execute_incremental_load`/`execute_shopee_window_refresh` (nunca roda concorrente com eles) → `lock_timeout`/`statement_timeout` → MESMO `LOCK TABLE ... SHARE ROW EXCLUSIVE MODE` → fingerprint fora do escopo (antes) → staging TEMP materializada com o MESMO SQL Shopee auditado do Gate S2/S3 → validações estruturais (duplicidade, nulos, numerador≤denominador, NaN/negativo, escopo marketplace/janela, `zero_source_risk` — qualquer uma bloqueia, sem `--confirm-empty-window`, já que este modo nunca precisa de uma exceção para avançar) → key-diff (`gold_only`/`source_only`/`changed` → `would_change_data`) → fingerprint fora do escopo reconferido (depois, deve bater com antes) → `ROLLBACK` sempre → release do lock/close no `finally`. Se o `ROLLBACK` do caminho de sucesso falhar, o resultado NUNCA é `validated` silenciosamente — vira `failed` (não é possível confirmar que a transação encerrou limpa).

**Resultado próprio** (`ShopeeWindowWriteValidationResult`): `outcome` ∈ `validated|blocked|failed`; `staging_rows`, `gold_rows`, `gold_only_key_count`, `source_only_key_count`, `changed_key_count`, `structurally_safe_for_refresh`, `would_change_data`, `problems`, `warnings` — nunca linhas/chaves individuais.

**CLI — exit codes**: `0` validado (independente de `would_change_data`); `2` config/janela/secret/preflight inválido (nunca chega a abrir a transação de validação); `3` bloqueio estrutural/lock; `4` falha inesperada (rollback executado). Ordem da CLI: `DATAMART_DATABASE_URL` → janela → secret dedicado (`.env.gold-window-write.local`) → guardrails → preflight real → `validate_shopee_window_write_path`.

**Validado**: 34 testes novos (`pipelines/tests/test_gold_regional_window_write_validation.py`) cobrindo a ordem exata das operações, os caminhos reconciliado/divergente (por `gold_only`/`source_only`/`changed`) sempre com zero escrita persistente, cada validação estrutural bloqueando isoladamente, advisory lock ocupado, connect/autocommit/query falhando sem retry automático, rollback obrigatório no sucesso, rollback falhando no sucesso nunca retorna `validated`, release/close falhando nunca sugere retry, nenhuma chamada a commit/backup, nenhum `DELETE`/`INSERT` persistente (INSERT só na TEMP), a CLI validando janela antes de secret/preflight, preflight bloqueado impedindo a função, saída sem host/IP/URL/usuário/senha, `would_change_data=True` nunca chamando o refresh real, e a regressão estática dos símbolos proibidos. Suíte completa: **1338 testes**, sem regressão nos 6 arquivos focais de diagnose/refresh/restore/loader/write_conn/window_write_conn.

**Diferença em relação ao diagnose read-only (Gate S2/S2.1)**: o diagnose nunca abre conexão de escrita, nunca lê o secret dedicado, nunca cria staging, e recalcula a fonte via CTE a cada execução. Este modo novo abre a conexão de ESCRITA de verdade (mesma credencial `gold_shopee_window_writer`), adquire os mesmos locks, materializa a staging TEMP e roda as mesmas validações estruturais que o refresh real rodaria sob o lock — é um teste ponta a ponta do CAMINHO de escrita, não apenas da lógica de comparação.

**Status**: implementado e testado, **zero execução real** contra o Data Mart nesta rodada — nenhuma role/secret/GRANT criado ou alterado, nenhum refresh/restore/sync/pipeline/scheduler/deploy executado, nenhum commit/push feito. Não substitui o piloto real do Gate S4.3 (que ainda fará o `DELETE`/`INSERT` de verdade) — só permite validar secret/preflight/lock/staging/reconciliação com a credencial dedicada sem nenhum risco de escrita persistente, mesmo que a janela esteja divergente. A automação externa de scraping continua sem autorização para habilitar qualquer comando de escrita real.
