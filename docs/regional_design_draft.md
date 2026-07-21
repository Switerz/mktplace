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

### Gate S4.3b — primeira execução real do modo de validação (2026-07-20)

**Autorização explícita do Mário**: uma única execução de `--validate-shopee-window-write-path` na janela `2026-01-01..2026-01-07`, com a credencial dedicada `gold_shopee_window_writer`, locks e staging TEMP autorizados — sem refresh/restore/backup/DELETE/INSERT persistente/sync/retry.

**Prechecks**: HEAD confirmado, nenhum processo local de ingestão/refresh/restore ativo, nenhum advisory lock da Gold regional em uso (`pg_locks`, somente leitura, `count=0`). Preflight real (`window_write_conn.run_window_preflight()`) imediatamente antes: `report.ok=True`, `blocking_reasons=[]`, mesmo cluster físico da leitura, SSL ativo, atributos administrativos todos `false`, privilégios obrigatórios `true`, proibidos `false`. Snapshot read-only anterior (janela de controle): `--diagnose-shopee-window` — 512 linhas, `would_change_data=False`, `structurally_safe_for_refresh=True` (idêntico ao Gate S4.1); fingerprint agregado independente das 13 colunas de negócio da janela capturado à parte (fora do fingerprint de escopo já usado internamente pelo código).

**Execução única**: `python -m pipelines.ingestion.gold_regional.loader --validate-shopee-window-write-path --date-from 2026-01-01 --date-to 2026-01-07`, uma única vez, sem `--audit-path`/`--confirm-empty-window`. Início 2026-07-20T14:18:17Z, fim 2026-07-20T14:18:23Z (~6s), **exit code 4** (`outcome=failed`, rollback executado).

**Achado real (infraestrutura, não é bug de lógica)**: `CREATE TEMP TABLE stg_marketplace_region_daily` (a mesma staging já auditada do Gate S2/S3) dispara um **event trigger de DDL do AWS DMS** (`awsdms_intercept_ddl()`) que tenta `INSERT` em `public.awsdms_ddl_audit` para registrar o comando — e a role `gold_shopee_window_writer` (corretamente escopada como least-privilege) **não tem `INSERT`** nessa tabela de auditoria do DMS, que nunca fez parte do conjunto de GRANTs documentado (nem deveria — não é um objeto de negócio Shopee/Gold). Resultado: `permission denied for table awsdms_ddl_audit`, capturado e sanitizado por `sanitize_error_message` (nenhum host/IP/usuário/senha exposto). `staging_rows=0`, `gold_rows=0`, `structurally_safe_for_refresh=False`, `would_change_data=False` — todos os valores padrão do resultado, porque a falha ocorreu ANTES de qualquer agregado real ser calculado (a própria `CREATE TEMP TABLE` já falhou).

**Por que isso é exatamente o valor do Gate S4.3a/S4.3b**: `execute_shopee_window_refresh` (o futuro comando real `--refresh-shopee-window`) usa a MESMA `SQL_CREATE_STAGING` sob a MESMA credencial — ou seja, o piloto real do Gate S4.3 bateria nesse MESMO bloqueio, antes de chegar perto de qualquer `DELETE`/`INSERT` na Gold. Descobrir isso agora, num modo garantidamente somente-ROLLBACK, é precisamente o motivo de o Gate S4.3a existir em vez de ir direto para o piloto real.

**Validação pós-execução (somente leitura)**: nenhum advisory lock remanescente (`count=0`, igual ao antes); `--diagnose-shopee-window` na mesma janela — **idêntico** ao snapshot anterior (512 linhas, `would_change_data=False`, `structurally_safe_for_refresh=True`); fingerprint agregado independente das 13 colunas — **idêntico byte a byte** ao capturado antes da execução (diff automatizado confirmou arquivos idênticos); preflight repetido — role e privilégios **inalterados** (mesmíssimo resultado do preflight pré-execução); nenhum backup `.json`/`.sha256` novo produzido (o caminho nunca chegou perto da publicação de backup); `git status` sem nenhum arquivo novo.

**Tratamento da falha**: execução única, **sem retry automático** (conforme autorizado); nenhuma role/GRANT/secret alterado nesta rodada para tentar contornar o achado; rollback e liberação de lock/conexão confirmados sem avisos de falha.

**Status**: `--validate-shopee-window-write-path` funcionou exatamente como desenhado — **preveniu, sem nenhum risco de escrita persistente, uma tentativa real de refresh que teria falhado do mesmo jeito**. O piloto real do Gate S4.3 continua bloqueado por dois motivos agora, não só um: (1) ausência de janela candidata (Gate S4.1); (2) a role dedicada precisa de um GRANT adicional (`INSERT` em `public.awsdms_ddl_audit`, ou outra forma de contornar o trigger do DMS) antes de qualquer `CREATE TEMP TABLE` funcionar sob essa credencial — **nenhum GRANT foi alterado nesta rodada**; a decisão sobre como resolver isso fica para o Mário, com autorização explícita separada.

### Gate S4.3c — diagnóstico read-only do event trigger AWS DMS (2026-07-20)

**100% somente leitura** — nenhum GRANT/REVOKE/ALTER/CREATE/DROP executado, nenhuma role/função/trigger alterada.

**Event trigger**: `awsdms_intercept_ddl`, evento `ddl_command_end`, `evtenabled='O'` (dispara em modo origin normal), `evttags=NULL` (intercepta **todos** os comandos DDL, sem filtro por tag — inclui `CREATE TABLE`/`CREATE TEMP TABLE`, que Postgres marca com a mesma tag `CREATE TABLE`; não existe exclusão nativa para objetos temporários nesse mecanismo).

**Função do trigger** (`public.awsdms_intercept_ddl`, owner `postgres`, `plpgsql`):
- **`prosecdef = false`** — **SECURITY INVOKER**, não `SECURITY DEFINER`. Este é o achado central.
- `proconfig` (search_path fixo na função): nenhum configurado.
- Corpo qualifica **explicitamente** `public.awsdms_ddl_audit` (schema completo, não depende do `search_path` de quem chama) — descarta risco de sequestro por `search_path`.
- Corpo faz `INSERT` seguido de `DELETE` (limpeza do próprio registro após o log) — padrão consistente com o script de referência da AWS.
- Sem bloco `EXCEPTION` — qualquer falha de permissão propaga e aborta o comando DDL inteiro (foi exatamente o que aconteceu no Gate S4.3b).
- `sha256` da definição registrado internamente para referência futura (não reproduzido aqui).

**Tabela/sequence de auditoria**: `public.awsdms_ddl_audit` (colunas `c_key, c_time, c_user, c_txn, c_tag, c_oid, c_name, c_schema, c_ddlqry` — schema oficial do artefato AWS DMS), owner `postgres`, RLS desabilitado, sequence `public.awsdms_ddl_audit_c_key_seq` também owner `postgres` — **owner da função == owner da tabela == owner da sequence** (os três `postgres`).

**Privilégios efetivos**: owner (`postgres`) tem `INSERT`/`DELETE`/`SELECT`/`USAGE` completos (esperado, é o dono). `gold_shopee_window_writer`: nenhum dos quatro. **Nenhum grant a `PUBLIC`** em nenhum dos dois objetos. ACL completa da tabela mostra grants amplos de DML (`INSERT`/`DELETE`/`SELECT`/`UPDATE`/`TRUNCATE`) só para `postgres`, `datamart_rw` e `web_user` (padrão de privilégio genérico de schema `public`, não um artefato pensado especificamente para DMS); `datamart_ro`/`postgrest`/`sql_runner` só têm `SELECT`. Confirmado que `airflow` (via `datamart_rw`) e `web_user` conseguem `INSERT`/`DELETE`, mas **`shopee_raw_silver_writer`** (a role já existente da automação externa Raw/Silver) **também não tem** esses privilégios — ou seja, esta não é uma lacuna exclusiva do `gold_shopee_window_writer`: qualquer role nova, corretamente escopada como least-privilege e sem ser membro de `datamart_rw`/`web_user`, bateria no mesmo bloqueio ao rodar qualquer DDL (não só `CREATE TEMP TABLE` do Shopee).

**Comparação com o padrão oficial AWS DMS**:

| Item | Padrão AWS | Encontrado | Divergência |
|---|---|---|---|
| Função `SECURITY DEFINER` | Sim | **Não** (`prosecdef=false`) | **Sim** — causa raiz |
| `INSERT` + `DELETE` de limpeza no corpo | Sim | Sim | Não |
| Event trigger em `ddl_command_end` | Sim | Sim | Não |
| Artefatos no schema `public` | Sim | Sim | Não |
| Permissão suficiente para QUALQUER role rodar DDL | Sim (garantido pelo `SECURITY DEFINER`) | **Não** (depende de grants ad hoc por role) | **Sim** — consequência direta do item acima |

**Causa classificada: A** — "Função está como `SECURITY INVOKER` (`prosecdef=false`)", confirmado diretamente via `pg_proc.prosecdef`. Descartadas: B (não se aplica — o owner tem todos os privilégios necessários); C (referência já é schema-qualificada, sem depender de `search_path`); D (o trigger não foi customizado para excluir/incluir TEMP especificamente — simplesmente não distingue, mesmo comportamento que a AWS documenta para o script padrão).

**Opções avaliadas (nenhuma executada)**:
1. **Corrigir o artefato DMS** — `ALTER FUNCTION public.awsdms_intercept_ddl() SECURITY DEFINER`. Único comando necessário: como o owner (`postgres`) já tem `INSERT`/`DELETE`/`USAGE` completos na tabela/sequence e o corpo já qualifica o schema explicitamente, virar `SECURITY DEFINER` resolve para **qualquer** role, presente ou futura, sem exigir nenhum GRANT adicional em lugar nenhum. É exatamente o padrão que a própria AWS documenta — alinha o artefato ao seu próprio contrato, não introduz privilégio novo em nenhuma role de negócio.
2. **Conceder `INSERT`/`DELETE`/`USAGE` (sequence) ao `gold_shopee_window_writer`** — resolveria só para esta role, mas: (a) exigiria replicar o mesmo GRANT para toda role least-privilege futura que rode qualquer DDL; (b) dá a uma credencial de negócio escopada para Shopee acesso de escrita a uma tabela de auditoria de DDL **de todo o cluster** (poderia inserir/apagar linhas arbitrárias no log de auditoria de qualquer schema, não só Shopee) — risco real de falsificação de auditoria, e uma violação do princípio de least-privilege já adotado neste projeto (Gate S4.1/S4.2).
3. **Reescrever o loader para não usar `CREATE TEMP TABLE`** — maior complexidade e risco: a staging TEMP (`ON COMMIT DROP`) é o mecanismo de isolamento/atomicidade já auditado em `execute_first_load`/`execute_incremental_load`/`execute_shopee_window_refresh`/o novo modo de validação (Gate S2/S3/S4.3a); trocar por staging persistente ou CTEs repetidos tocaria código já testado e validado em centenas de testes, sem eliminar o problema de fundo (o mesmo bloqueio apareceria em qualquer outro DDL futuro).

**Recomendação**: Opção 1 (corrigir o artefato DMS para `SECURITY DEFINER`, seu próprio padrão documentado) — menor risco, menor blast radius, resolve de forma definitiva e uniforme, e é a única opção que não amplia privilégio de nenhuma role de negócio nem toca código já auditado. **Não implementada nesta rodada** — depende de decisão e autorização explícita do Mário, e por ser um artefato de infraestrutura AWS DMS (fora do escopo deste repositório), provavelmente também de quem administra a réplica/DMS.

**Integridade confirmada (somente leitura)**: nenhuma ACL alterada; nenhuma função/trigger alterado; role `gold_shopee_window_writer` com atributos/privilégios idênticos ao preflight anterior; nenhum objeto criado; nenhum advisory lock remanescente (`count=0`); Gold/Silver não tocadas nesta rodada (só catálogo do sistema e a tabela de auditoria DMS foram consultados, sempre via `SELECT`).

**Próximo gate, à época proposto**: aplicar `ALTER FUNCTION public.awsdms_intercept_ddl() SECURITY DEFINER` mediante autorização explícita separada, depois repetir uma única execução real de `--validate-shopee-window-write-path` para confirmar que o `CREATE TEMP TABLE` deixa de ser bloqueado. (Nota: o que era "S4.3d" aqui virou o Gate S4.3d abaixo, que fortalece o *preflight* para detectar esse cenário antes de qualquer ação real; a correção em si virou o Gate S4.3e — **já executada e concluída com sucesso**, ver seção correspondente mais abaixo.)

### Gate S4.3d — preflight detecta incompatibilidade do interceptor AWS DMS (2026-07-20)

**100% implementação/testes/documentação — zero conexão com banco real, zero ALTER FUNCTION/GRANT/REVOKE, zero mudança de role/secret.**

**Objetivo**: fortalecer `window_write_conn.run_window_preflight()` para detectar, ANTES de qualquer staging/lock operacional, se o event trigger de auditoria de DDL do AWS DMS (achado do Gate S4.3b/S4.3c) está configurado de forma incompatível com uma role least-privilege — sem nunca exigir/sugerir privilégio de auditoria DMS para `gold_shopee_window_writer`.

**Checagens adicionadas** (mesma sessão read-only já existente do preflight, mesma regra "inconclusivo bloqueia"):
- `awsdms_intercept_ddl` existe (`pg_event_trigger`) e está habilitado (`evtenabled <> 'D'`);
- se ausente OU desabilitado: **nunca bloqueia** (`dms_ddl_interceptor_compatible=True`) — o `CREATE TEMP TABLE` não seria interceptado;
- se presente e habilitado, exige TODOS como `True` explícito: função existe (`pg_proc` via `evtfoid`); `prosecdef=true` (`SECURITY DEFINER`); tabela `public.awsdms_ddl_audit` existe (via `pg_class`/`pg_namespace`, não `information_schema`, para nunca colidir com a checagem já existente da tabela Gold); sequence associada existe (`pg_get_serial_sequence`); o **owner da função** (nunca a role conectada, verificado via OID — o nome do owner nunca é lido nem impresso) tem `INSERT`/`DELETE` na tabela e `USAGE` na sequence.

**Novos campos no `safe_summary`** (só booleanos/`None`): `dms_ddl_trigger_present`, `dms_ddl_trigger_enabled`, `dms_function_present`, `dms_function_security_definer`, `dms_audit_table_present`, `dms_audit_sequence_present`, `dms_function_owner_can_insert_audit`, `dms_function_owner_can_delete_audit`, `dms_function_owner_can_use_sequence`, `dms_ddl_interceptor_compatible`.

**Mensagem de bloqueio** — genérica e sanitizada, lista só QUAIS itens falharam (nunca owner/corpo da função/host/IP/URL/credencial), ex.: `"Interceptor DDL do AWS DMS incompatível com execução least-privilege: função sem SECURITY DEFINER (ou não confirmado)"`.

**Como isso teria bloqueado o Gate S4.3b antes do `CREATE TEMP TABLE`**: no estado real do cluster (confirmado no Gate S4.3c), o trigger está presente e habilitado, mas `prosecdef=false`. Com o novo preflight, `run_window_preflight()` já teria retornado `report.ok=False` com o motivo `"Interceptor DDL do AWS DMS incompatível... função sem SECURITY DEFINER"` **antes** de `run_refresh_shopee_window_cli`/`run_validate_shopee_window_write_path_cli` chegarem a chamar `execute_shopee_window_refresh`/`validate_shopee_window_write_path` — ou seja, a tentativa real do Gate S4.3b teria parado no preflight (exit 2), nunca chegando a abrir a transação de escrita, adquirir o advisory lock, nem tentar o `CREATE TEMP TABLE` que de fato falhou. O comportamento observado no S4.3b (falha dentro da transação, com rollback) continua correto e seguro, mas o novo preflight move essa mesma detecção para **antes** de qualquer lock/staging — mais barato e mais rápido de diagnosticar.

**Testes**: 13 novos em `pipelines/tests/test_gold_regional_window_write_conn.py` (trigger ausente não bloqueia; trigger desabilitado não bloqueia; habilitado + `SECURITY INVOKER` bloqueia — reproduz o achado real do S4.3b; `SECURITY DEFINER` + privilégios corretos aprova; ausência isolada de `INSERT`/`DELETE`/`USAGE` bloqueia cada uma; tabela/sequence/função ausente bloqueia cada uma; resultado inconclusivo — exceção a meio das consultas — bloqueia; mensagem nunca vaza owner/corpo/infraestrutura; regressão confirmando que um cluster sem o artefato DMS se comporta exatamente como antes do Gate S4.3d) + 2 testes de integração (`test_gold_regional_window_refresh.py`/`test_gold_regional_window_write_validation.py`) confirmando que `execute_shopee_window_refresh`/`validate_shopee_window_write_path` **nunca são chamados** quando o preflight bloqueia por incompatibilidade DMS. Suíte completa: **1353 testes**, sem regressão nos preflights existentes (os 73 testes originais de `window_write_conn` continuam passando sem nenhuma alteração, porque um cluster fake sem resposta programada para o event trigger é tratado corretamente como "trigger ausente").

**Status (nesta rodada do Gate S4.3d)**: implementado e testado, **zero conexão com banco real**. A correção de fato (`ALTER FUNCTION ... SECURITY DEFINER`) ainda não tinha sido executada aqui — isso aconteceu depois, no Gate S4.3e (ver seção abaixo), com autorização explícita separada e resultado bem-sucedido.

### Gate S4.3e — correção real do interceptor DMS + primeira validação sem bloqueio (2026-07-20)

**Autorização explícita do Mário**: executar o `ALTER FUNCTION public.awsdms_intercept_ddl() SECURITY DEFINER` uma única vez, com a credencial administrativa do Gate S4.2, seguido de uma única execução real de `--validate-shopee-window-write-path` na janela de controle — sem refresh/restore/backup/sync/GRANT/mudança de owner-corpo-assinatura/retry.

**Precheck**: HEAD/`git status`/secrets/locks confirmados como nas rodadas anteriores. Preflight real ANTES do ALTER: `report.ok=False`, **único** bloqueio: `"Interceptor DDL do AWS DMS incompatível com execução least-privilege: função sem SECURITY DEFINER (ou não confirmado)"` — todos os demais checks (`rolsuper`/`rolcreatedb`/.../`dms_function_owner_can_insert_audit`/`can_delete_audit`/`can_use_sequence`, todos os privilégios obrigatórios da Gold) já `True`/`False` como esperado. Confirmado: a incompatibilidade DMS era a **única** causa de bloqueio.

**Verificação pré-ALTER** (mesma transação, antes do `ALTER`): função `public.awsdms_intercept_ddl()` — 1 único overload, `pronargs=0`, `prorettype=event_trigger`, `prosecdef=false` (bate com o Gate S4.3c), hash do corpo idêntico ao capturado no Gate S4.3c. `oid`/owner reconferidos contra os valores já auditados.

**`ALTER FUNCTION public.awsdms_intercept_ddl() SECURITY DEFINER`** — a **única mudança real** desta rodada: `prosecdef` de `false` para `true`. Executado **uma única vez**, dentro de uma transação controlada (`autocommit=False`), com credencial que se confirmou membro da role owner da função (sem precisar de superuser). Verificação **na mesma transação, antes do commit**: `prosecdef=true`; `oid`/owner/`pronargs`/`prorettype`/hash do corpo **idênticos** a antes (nenhuma mudança de assinatura, corpo ou dono); event trigger continua `ddl_command_end`, habilitado, ligado ao mesmo `oid`; privilégios de `gold_shopee_window_writer` em `public.awsdms_ddl_audit` (`INSERT`/`DELETE`) **inalterados** (continuam `false`); contagem de grants a `PUBLIC` **inalterada** (zero, como antes). Todos os 14 checks pós-ALTER bateram → `COMMIT`.

**Verificação pós-ALTER independente** (sessão nova, somente leitura): `prosecdef=true` confirmado de forma persistente; `oid`/`pronargs`/`prorettype`/hash do corpo idênticos; `proconfig`/`search_path` continua sem nenhuma configuração (idêntico ao Gate S4.3c — o `ALTER` não toca nisso); event trigger inalterado; ACL completa de `public.awsdms_ddl_audit`/sequence **byte a byte idêntica** à capturada no Gate S4.3c (nenhum grant novo para `gold_shopee_window_writer`, `shopee_raw_silver_writer` ou `PUBLIC`).

**Preflight real pós-ALTER**: `report.ok=True`, `blocking_reasons=[]`, `dms_function_security_definer=true`, `dms_ddl_interceptor_compatible=true` — todos os demais campos idênticos ao preflight pré-ALTER.

**Execução única de `--validate-shopee-window-write-path`** na janela `2026-01-01..2026-01-07`: início `2026-07-20T17:50:51Z`, fim `2026-07-20T17:51:04Z` (~13s), **exit code 0**. Resultado: `outcome=validated`, `staging_rows=512`, `gold_rows=512`, `gold_only_key_count=0`, `source_only_key_count=0`, `changed_key_count=0`, `structurally_safe_for_refresh=true`, `would_change_data=false`. O `CREATE TEMP TABLE` que falhou no Gate S4.3b **funcionou desta vez** — confirma que a correção resolveu exatamente o bloqueio diagnosticado.

**Validação pós-execução (somente leitura)**: nenhum advisory lock remanescente (`count=0`); `--diagnose-shopee-window` na mesma janela **idêntico** ao snapshot anterior (512 linhas, `would_change_data=False`); fingerprint agregado independente das 13 colunas **idêntico byte a byte** ao capturado antes (`diff` confirmou arquivos idênticos); nenhum backup `.json`/`.sha256` novo (não seria produzido por este modo de qualquer forma); `git status` sem nenhum arquivo novo.

**Status**: `awsdms_intercept_ddl()` agora `SECURITY DEFINER`, alinhado ao contrato oficial da AWS. `gold_shopee_window_writer` nunca recebeu nem precisou de privilégio na infraestrutura DMS — nenhum `GRANT`/`REVOKE` foi executado em lugar nenhum. O caminho de escrita da Gold regional Shopee (secret → preflight → advisory lock → table lock → staging TEMP → validações → reconciliação → rollback) está **confirmado funcional ponta a ponta com a credencial dedicada**, sem nenhuma escrita persistente. **Zero `--refresh-shopee-window`, zero `--restore-shopee-window`, zero backup, zero sync Neon executado nesta rodada** — a única escrita real foi o `ALTER FUNCTION` em si; a validação do caminho terminou sempre em `ROLLBACK`. O que falta para o piloto real do Gate S4.3 (o `DELETE`/`INSERT` de verdade, persistente) é só a ausência de janela candidata (Gate S4.1) — não há mais bloqueio de infraestrutura conhecido. **Nada foi commitado nesta rodada.**

### Gate S5.1 — diagnóstico do contrato operacional Shopee Raw→Silver→Gold (2026-07-20, revisado no Gate S5.1b)

**100% diagnóstico e leitura de código/documentação — zero conexão com banco, zero alteração de role/secret/DMS, zero código alterado.** Resposta completa (16 perguntas, contrato, sequência, recomendação) entregue no turno de chat correspondente; esta seção registra o resumo executivo para referência futura. **Revisado no Gate S5.1b** após três correções de desenho apontadas antes de qualquer implementação (ver abaixo) — nenhum código foi alterado em nenhuma das duas rodadas.

**O que já está pronto** (evidenciado em `pipelines/ingestion/gold_regional/loader.py`/`window_write_conn.py`, testado em 1353 testes e validado com execução real nos Gates S4.2–S4.3e): secret dedicado + preflight (incluindo a checagem DMS do Gate S4.3d) + advisory lock + table lock + staging TEMP + validações estruturais recalculadas sob lock + comparação por chave + backup atômico pré-DELETE + DELETE/INSERT escopados + reconciliação pós-insert + fingerprint fora do escopo + rollback-por-padrão + modo de validação sem persistência — tudo com resultado machine-readable (`outcome`/dataclass) e exit codes distintos (0/2/3/4) e sem nenhum retry automático embutido. `load_shopee_raw.py --apply --backfill` (Raw) também já é idempotente por hash, com preflight e reconciliação pós-carga via primary.

#### Correção 1 (Gate S5.1b) — a decisão autoritativa nunca pode depender da réplica

**Rastro no código** (evidência exata):
- `run_diagnose_shopee_window_cli` (`loader.py:2806`) usa `read_url = settings.datamart_url` — isto é, `DATAMART_DATABASE_URL`. Este é o mesmo valor que `sync_region_daily.py` documenta explicitamente como **read replica**, com um incidente real já registrado: *"DATAMART_DATABASE_URL é uma read replica (achado desta fase: pg_is_in_recovery()=true) [...] usar a réplica aqui já causou uma leitura incompleta que parecia um problema real quando era só lag"*.
- `run_refresh_shopee_window_cli` (`loader.py:2932`) resolve `write_url` a partir do secret dedicado (`window_write_conn.validate_window_write_guardrails`), nunca de `settings.datamart_url`. O preflight (`window_write_conn.run_window_preflight`, linha ~349) **bloqueia explicitamente** se `pg_is_in_recovery()` do `write_url` não for confirmado `false` — ou seja, o próprio preflight impede que `write_url` seja uma réplica. `write_url` é sempre o **primary**.
- Dentro de `execute_shopee_window_refresh` (`loader.py:2074`+), a staging (`stg_marketplace_region_daily`, populada a partir de `silver.stg_shopee_order_item_snapshots`) e a leitura de `gold.marketplace_region_daily` para o key-diff (`SQL_REFRESH_KEY_DIFF`, linha 2208) rodam **na mesma conexão** (`write_url`, o primary), **na mesma transação**, sob o mesmo `LOCK TABLE ... SHARE ROW EXCLUSIVE MODE` adquirido logo antes. Não há leitura de réplica em nenhum ponto desta função.
- Ordem exata confirmada em código (linhas 2208–2242): `SQL_REFRESH_KEY_DIFF` decide `would_change_data`; se `False`, o `return` de `outcome="no_op"` (linha 2212-2215) acontece **antes** de qualquer `SELECT` das linhas para backup (linha 2217), antes de `_write_window_backup_atomic` (linha 2223) e antes de `SQL_REFRESH_DELETE` (linha 2242).

**Respostas diretas**:
- *Um wrapper que usa diagnose na réplica para decidir não chamar o refresh pode perder um lote recém-carregado?* **Sim.** Se a Silver acabou de ser escrita (via primary) e o diagnose lê a réplica antes da replicação alcançar esse ponto, `would_change_data` pode aparecer `False` quando já é `True` no primary — exatamente a classe de bug que `sync_region_daily.py` já documentou como incidente real (só que lá era sobre a Raw; aqui seria sobre a Gold).
- *Chamar diretamente o refresh autoritativo no primary é seguro quando não há mudança?* **Sim.** O único custo de um `no_op` é abrir a transação, adquirir o advisory lock + table lock e materializar a staging — tudo isso é revertido por `ROLLBACK` (via `_rollback_best_effort`) antes de qualquer escrita persistente.
- *No caminho `no_op`, há zero backup e zero DELETE/INSERT persistente?* **Sim, confirmado pela ordem de código acima** — `no_op` retorna estritamente antes das três operações (SELECT para backup, escrita do backup, `DELETE`).

**Comparação A/B/C**:
- **A** (diagnose na réplica → refresh só se divergir): rejeitada — risco real e já precedentemente confirmado de falso `no_op` por lag.
- **B** (novo diagnose read-only no primary → refresh só se divergir): elimina o risco de réplica, mas duplica lógica (um segundo cálculo de `would_change_data` fora da transação de escrita) sem necessidade — `execute_shopee_window_refresh` já faz exatamente esse cálculo, sob lock, de forma mais forte (nada pode mudar entre o diagnose e o refresh porque não há intervalo).
- **C** (chamar `execute_shopee_window_refresh` diretamente no primary; ele decide `committed`/`no_op` sob lock com staging autoritativa): **recomendada** — é a opção mais simples que não permite falso `no_op` por lag, porque a função já faz exatamente isso e já está implementada, testada e validada em execução real.

**Correção aplicada**: a recomendação deste gate deixa de ser "replicar `sync_region_if_needed.py` com um diagnose-then-conditional-refresh" e passa a ser **opção C** — o wrapper futuro (Gate S5.3, renumerado) deve chamar `execute_shopee_window_refresh` diretamente contra o primary, sem nenhum diagnose read-only prévio decidindo se vale a pena chamá-lo. Um diagnose continua útil só como ferramenta de inspeção humana (não como gate de decisão automatizada).

#### Correção 2 (Gate S5.1b) — procedimento Data Mart → Neon

Depois que a automação externa atualizar a Gold Shopee, a Torre deve executar **somente**:

```
python -m pipelines.ops.sync_region_if_needed
```

**Não** encadear `python -m pipelines.ingestion.gold_regional.loader --incremental` antes disso como pré-requisito. Motivo: `--incremental` (`execute_incremental_load`) resolve **avanço por data nova** (`date > MAX(date)` já carregado) para ML e Shopee juntos — ele não teria motivo para rodar só por causa de um refresh de janela histórica Shopee (que tipicamente corrige datas **já** carregadas, não avança a data máxima), e rodá-lo automaticamente antes do sync criaria uma dependência artificial entre dois mecanismos que resolvem problemas diferentes (avanço incremental vs. correção retroativa por janela). `sync_region_if_needed.py` já lê `gold.marketplace_region_daily` como fonte (via `fetch_source_rows`) independente de qual caminho a atualizou (`--incremental` ou `--refresh-shopee-window`) — não precisa que os dois rodem em sequência.

#### Correção 3 (Gate S5.1b) — identidade do lote, antes de qualquer wrapper

**Rastro no código** (`pipelines/ingestion/shopee_raw/writer.py`):
- `FileWriteOutcome` (linha 46) já tem um campo `file_id: Optional[int]`, preenchido tanto para `outcome="inserted"` (linha 162) quanto para `outcome="skipped_idempotent"` (linha 84) — ou seja, **todo arquivo processado por `load_shopee_raw.py --apply --backfill` já retorna seu `file_id` real**, mesmo quando pulado por idempotência.
- `batch_id` (`writer.new_batch_id()`, um UUID novo por invocação da CLI) é **persistido em `raw.shopee_ingestion_file.batch_id`** (linha 133/146) para cada arquivo daquela execução — já é o agrupador real de "um lote pode conter múltiplos arquivos" (`run_apply_backfill` processa N arquivos elegíveis numa única invocação, todos com o mesmo `batch_id`).
- `batch_id` **não** é propagado até a Silver (confirmado: nenhuma coluna `batch_id` em `pipelines/staging/shopee/mapping.py`); `file_id`, sim, é (já usado pelo dedup da Gold em `loader.py`, via `silver.stg_shopee_order_item_snapshots.file_id`).

**Conclusão — nenhum identificador novo precisa ser inventado**: a identidade do lote já existe e é **`batch_id` (agrupador do Raw) + a lista de `file_id`s** retornada por `writer.insert_file`/`run_apply_backfill`. O contrato do Gate S5.2 (helper de resolução de janela) deve:
1. Receber a lista de `file_id`s do lote (de um `batch_id`, ou passada diretamente pelo chamador).
2. Confirmar que **todos** chegaram à Silver: comparar contagem de linhas em `raw.shopee_order_item_export WHERE file_id = ANY(...)` contra `silver.stg_shopee_order_item_snapshots WHERE file_id = ANY(...)` (mesmo critério de reconciliação já usado por `build_sql.py`/`validations.post_insert_check`).
3. Só então calcular `MIN(order_created_at)::date`/`MAX(order_created_at)::date` em `silver.stg_shopee_order_item_snapshots WHERE file_id = ANY(...)` — **nunca** pela data do arquivo.
4. Bloquear (nunca inferir um resultado parcial) se: algum `file_id` do lote tiver `outcome="failed"` no retorno do backfill; a contagem Raw×Silver não bater para algum `file_id`; ou a lista de `file_id`s vier vazia.

Isso é 100% somente leitura e não depende de nenhum runner Silver novo — só precisa que a Silver já esteja reconciliada (responsabilidade que já existe, de um lado ou de outro, ver Correção 5 abaixo).

#### Correção 4 (Gate S5.1b) — próximos gates reordenados

- **S5.2**: resolver a janela afetada por lote/`file_id`s (Correção 3), 100% read-only, machine-readable (função + testes com fakes).
- **S5.3**: wrapper operacional único, chamando `execute_shopee_window_refresh` diretamente contra o primary (Correção 1, opção C) — `committed`/`no_op`/`blocked`/`failed`, sem diagnose-then-conditional prévio.
- **S5.4**: receipt JSON, `run_id`, commit do repositório, timestamps estruturados, convenção segura de `audit_path` (diretório, nome único, retenção).
- **S5.5**: decidir se o runner Silver externo precisa ser trazido para este repositório ou se basta formalizar sua interface (ver Correção 5).
- **S5.6**: runbook operacional final + teste integrado ponta a ponta sem nenhuma escrita persistente.
- **S6**: primeiro refresh persistente real, só quando surgir uma janela genuinamente divergente.

Esta ordem substitui a lista anterior (que colocava o wrapper antes de resolver a janela).

#### Correção 5 (Gate S5.1b) — runner Silver: não é bloqueio automático

Separação explícita:
- **Requisito de integração** (obrigatório, já satisfeito estruturalmente): a Silver precisa terminar reconciliada e fornecer os identificadores do lote (`file_id`s) — isso já é verdade independente de onde o SQL roda, porque `file_id`/`raw_id` já são colunas reais da Silver e a reconciliação já é um critério documentado (`build_sql.py`/`docs/shopee_datamart_daily_jobs_handoff.md` §7.5).
- **Melhoria de governança** (não bloqueante): trazer o runner (execução de `db/sql/staging/shopee_staging_transform.sql`) para dentro deste repositório, versionado e testado, em vez de a automação externa manter seu próprio wrapper com um workaround de lock.
- **Bloqueio real** (só se confirmado): apenas se o runner externo **não conseguir** fornecer os `file_id`s do lote processado ou garantir/confirmar que a carga terminou (success-only, sem parcial). **Isso ainda não foi confirmado nem negado** — o código do runner externo está fora deste repositório e não foi inspecionado nesta auditoria. Até essa confirmação, a ausência de um runner Silver *neste repo* é reclassificada de "bloqueante" para **"verificação pendente"**.

**Decisão arquitetural reafirmada, sem alteração**: a automação externa não recebe e não deve receber, em nenhum gate futuro, a credencial administrativa nem `DATABASE_URL` (Neon) — ela opera só com `gold_shopee_window_writer`/`.env.gold-window-write.local` (Gold) e a role dedicada de Raw/Silver já existente. O sync Data Mart → Neon continua exclusivamente sob responsabilidade da Torre, via `sync_region_if_needed` (Correção 2).

**Lacunas ainda válidas do S5.1 original** (com a Correção 3 já resolvendo o item 2 conceitualmente — falta só implementar): saída `--json` nas CLIs de janela Shopee; evidência de execução sem `run_id`/commit/timestamps estruturados; `docs/shopee_datamart_daily_jobs_handoff.md` desatualizado (propõe `--refresh-full` inexistente e acesso a Neon — preservado sem alteração, mas não deve ser seguido ao pé da letra); ausência de template de `.env` escopado à automação (o `.env` principal mistura `DATABASE_URL` do Neon com `DATAMART_DATABASE_URL`).

**Lacunas não bloqueantes (melhorias)**: retenção/limpeza dos backups `.json`/`.sha256`; runbook operacional em linguagem de operação; SLA explícito de sincronização Gold→Neon quando o scheduler for reativado (`full_daily` continua desabilitado).

#### Pergunta central revisada (Gate S5.1b)

> *Qual é o menor código que ainda falta para a automação externa atualizar a Gold de forma autônoma, sem depender de réplica, interpretação de stdout humano ou escolha manual da janela?*

Dois pedaços pequenos, nenhum deles tocando lógica de transação/segurança já auditada:
1. **Uma função read-only** que recebe uma lista de `file_id`s (já retornados por `load_shopee_raw.py`), confirma que todos chegaram à Silver reconciliada, e devolve `MIN`/`MAX(order_created_at)` como janela candidata — ou um sinal claro de "lote incompleto/parcial" (Gate S5.2).
2. **Um wrapper fino** que recebe essa janela e chama `execute_shopee_window_refresh` diretamente contra o primary — sem diagnose prévio na réplica —, devolvendo `committed`/`no_op`/`blocked`/`failed` como JSON + exit code (Gate S5.3).

Tudo o mais (locks, staging autoritativa, backup atômico, rollback, validação estrutural, decisão sob lock) **já existe, já está testado e já foi validado em execução real**. O risco de handoff continua sendo de **automação/orquestração incompleta**, não de segurança — e a Correção 1 deste gate era a única lacuna que, se implementada do jeito originalmente sugerido (opção A/B com diagnose na réplica), teria introduzido um risco real novo.

### Gate S5.2 — resolução read-only da janela Gold Shopee por `file_id`s do lote (2026-07-20)

**Implementado, testado, zero conexão com banco real, zero alteração de `loader.py`/`window_write_conn.py`/SQL auditado.** Novo módulo pequeno: `pipelines/ingestion/gold_regional/shopee_batch_window.py`.

**Inspeção prévia (evidência de código, antes de implementar)**:
- `file_id` é `bigint NOT NULL` em `silver.stg_shopee_order_item_snapshots` (`db/sql/staging/shopee_staging_ddl.sql:31`) — a CLI aceita `--file-id` como inteiro na faixa `bigint` do Postgres, nunca assumindo `int` de 32 bits.
- `order_created_at` é `timestamp NOT NULL` (sem timezone) — o transform Shopee da Gold (`loader.py`, `SQL_INSERT_SHOPEE_STAGING`/`_shopee_incremental_select`/`SQL_SHOPEE_WINDOW_RECALC_ROWS`) sempre usa `order_created_at::date`, sem conversão de timezone (BRT nativo, decisão já registrada no Gate 5). Este módulo usa exatamente o mesmo cast, nunca uma expressão nova.
- O limite de janela é reaproveitado de `loader._validate_shopee_window`/`MAX_SHOPEE_WINDOW_DAYS=180` — importado e chamado diretamente (leitura, sem alterar o módulo original), nenhum threshold divergente criado.

**Contrato da função**: `resolve_shopee_batch_window(write_url: str, file_ids: Sequence[int]) -> ShopeeBatchWindowResult`. Só consulta `silver.stg_shopee_order_item_snapshots` (2 `SELECT`s, ambos com `file_id = ANY(%(file_ids)s)` como bind parameter — nunca interpola IDs na string). Sessão sempre `readonly=True`, `isolation_level='REPEATABLE READ'`, `autocommit=False`; **sempre `ROLLBACK`** (inclusive no sucesso — nunca há nada a commitar); `close` garantido em `finally`; nenhuma exceção de rollback/close mascara o resultado já decidido (mesmo padrão de `validate_shopee_window_write_path`, Gate S4.3a). Nunca adquire advisory lock nem table lock (não é necessário — não compete com nenhuma escrita).

**Como evita janela parcial**: a lista de `file_id`s pedida é resolvida por inteiro ou não é resolvida — se `SELECT DISTINCT file_id ... WHERE file_id = ANY(...)` não retornar TODOS os IDs pedidos, o resultado é `blocked`/`missing_file_ids` e a segunda query (`MIN`/`MAX(order_created_at)`) **nunca roda** (confirmado em teste: nenhuma query de agregação é executada quando há ausência, total ou parcial). Zero linhas encontradas e `order_created_at` nulo (defensivo — a coluna é `NOT NULL`, mas o módulo nunca assume a constraint sem conferir) também bloqueiam antes de produzir uma janela.

**Por que o primary em sessão read-only, nunca a réplica**: mesma credencial dedicada do refresh (`gold_shopee_window_writer`), mesmo secret (`.env.gold-window-write.local`), mesmo preflight (`window_write_conn.run_window_preflight`) — que já confirma `pg_is_in_recovery()=false` antes de qualquer query. Isso elimina o risco de falso "lote incompleto" por lag de réplica identificado na Correção 1 do Gate S5.1b, sem abrir nenhuma chance de escrita (a sessão é `readonly=True` desde a conexão).

**Confirmação de zero escrita possível**: nenhuma chamada a `commit()`; nenhuma referência a `INSERT`/`UPDATE`/`DELETE`/`TRUNCATE`/`CREATE TABLE`/`CREATE TEMP`/`ALTER TABLE`/`DROP TABLE` em nenhuma das duas constantes SQL executadas; nenhuma chamada a `execute_shopee_window_refresh`/`execute_shopee_window_restore`/`sync_region_if_needed`/`sync_region_daily` (testes de regressão estática cobrem os dois pontos). A role `gold_shopee_window_writer` não ganha nem precisa de `SELECT` em `raw.*` — este módulo nunca referencia `raw.shopee_ingestion_file` em nenhuma query real (só em prosa, explicando por que não é preciso).

**Limitações que pertencem ao runner Silver, não a este módulo** (documentadas no próprio docstring do módulo): (1) este módulo NUNCA lê `raw.shopee_ingestion_file` nem tenta confirmar `batch_id` — a garantia de que a Silver está reconciliada e a lista de `file_id`s completa é responsabilidade de quem chama (o runner Silver, externo a este repositório nesta fase), que só deve invocar este componente **depois** de sua própria transação/reconciliação Raw→Silver terminar; (2) se o runner tiver feito uma carga PARCIAL **dentro** de um `file_id` que já existe na Silver (algumas linhas gravadas, outras não), este módulo não tem como detectar isso sem acesso à Raw — segue sendo garantia do contrato do runner Silver.

**CLI**: `python -m pipelines.ingestion.gold_regional.shopee_batch_window --file-id <id> [--file-id <id> ...] [--json]`. Validação de entrada (não vazio, inteiro na faixa bigint, sem duplicados, no máximo `MAX_BATCH_FILE_IDS=200`) roda **antes** de ler o secret ou conectar — confirmado em teste com `psycopg2.connect` armadilhado. Com `--json`, stdout recebe **um único documento JSON** (avisos e o resumo do preflight vão sempre para stderr). Exit codes determinados por uma tabela fixa `reason_code → exit code` (nunca lógica duplicada): `0` resolvido; `2` entrada/config/secret/preflight inválido (`invalid_input`, `datamart_url_not_configured`, `secret_load_error`, `preflight_blocked`); `3` lote/Silver estruturalmente indisponível ou janela incompatível (`missing_file_ids`, `empty_batch`, `null_order_date`, `window_exceeds_limit`); `4` falha inesperada (`unexpected_error`).

**Testes**: 33 novos em `pipelines/tests/test_gold_regional_shopee_batch_window.py` — validação pura de entrada (vazio, duplicado, tipo, bool-como-int, não positivo, acima do limite); 1 e múltiplos `file_id`s resolvidos com `MIN`/`MAX` conjunto; ausência total/parcial nunca calcula janela; zero linhas; data nula; janela dentro/fora do limite (reaproveitando `MAX_SHOPEE_WINDOW_DAYS`); bind parameters confirmados (ID não aparece interpolado na string SQL); só a tabela Silver esperada, nunca Raw/Gold/Neon; sessão `readonly`/`REPEATABLE READ`/`autocommit=False`; rollback no sucesso e best-effort na falha; `close` sempre, inclusive com `set_session` falhando; exceção original nunca mascarada; preflight bloqueado impede a query; erro nativo sanitizado; JSON como documento único parseável, sem PII/infraestrutura; regressão estática dupla (nenhum símbolo de escrita/refresh/sync no módulo; nenhuma das duas SQL constants referencia `raw.`/`gold.`/`marts.`). Suíte completa: **1386 testes**, sem regressão nos 7 arquivos focais de Gold regional (403 testes).

**Status**: implementado e testado, **zero conexão com banco real** nesta rodada. Não implementa ainda o wrapper (`refresh_shopee_window_if_needed`, Gate S5.3) nem o receipt/`audit_path` (Gate S5.4) — ambos continuam pendentes, agora com a janela resolvível de forma read-only e machine-readable como pré-requisito satisfeito.

### Gate S5.2.1 — hardening final da interface importável antes do commit (2026-07-20)

Revisão pré-commit do Gate S5.2 encontrou dois pontos reais na API Python pública (não na CLI, que já rodava o preflight): `resolve_shopee_batch_window(write_url, file_ids)` era importável e **não** executava `run_window_preflight` — um chamador futuro que passasse uma réplica reintroduziria o falso `missing_file_ids` por replication lag (a mesma classe de risco da Correção 1 do Gate S5.1b, agora do lado de leitura). E todo `InvalidWindowError` era mapeado para `window_exceeds_limit`, inclusive quando a causa real era `date_to` no futuro — reason_code enganoso.

**Correção 1 — preflight obrigatório dentro da própria função pública.** O módulo agora separa duas funções:
- **Privada** (`_resolve_shopee_batch_window_after_preflight(write_url, ids)`): detalhe de implementação, prefixo `_`, **fora de `__all__`**, nunca deve ser importada diretamente. Contém só a transação read-only e as duas consultas — assume que `ids` já foi validado e que o preflight já aprovou.
- **Pública** (`resolve_shopee_batch_window(write_url: str, datamart_read_url: str, file_ids: Sequence[int]) -> ShopeeBatchWindowResult`): **único contrato público do módulo** (`__all__` explícito). Ordem fixa e sem atalho: valida `file_ids` (bloqueia com `invalid_input` antes de qualquer I/O) → `window_write_conn.run_window_preflight(write_url, datamart_read_url)` (exceção sanitizada ou `report.ok is not True` → bloqueia com `preflight_blocked`, causa em `problems`, **a função privada nunca é chamada**) → só então delega para a função privada. Não existe parâmetro `skip_preflight`/`preflight_confirmed` nem qualquer outro booleano capaz de desarmar essa checagem.

**CLI**: `run_cli` não chama mais `run_window_preflight` diretamente — delega inteiramente para a função pública (que já roda o preflight internamente). Não há segundo caminho de consulta na CLI; o resumo de preflight bloqueado (quando houver) continua indo só para stderr, e o stdout com `--json` permanece um único documento.

**Correção 2 — reason_code genérico e verdadeiro para janela inválida.** `window_exceeds_limit` foi removido. `loader._validate_shopee_window` rejeita três causas distintas (`date_from > date_to`; `date_to` no futuro; janela > `MAX_SHOPEE_WINDOW_DAYS` dias) e o módulo nunca tenta adivinhar qual delas ocorreu — o novo reason_code único e genérico é **`refresh_window_invalid`**, com a causa exata sanitizada preservada em `problems`. Uma data futura nunca é (e nunca foi, a partir desta correção) classificada como "janela grande demais".

**Testes**: suíte reescrita para o novo contrato — 40 testes em `test_gold_regional_shopee_batch_window.py` (era 33), incluindo: chamada direta da função pública com preflight bloqueado nunca conecta nem consulta (`psycopg2.connect` armadilhado); com preflight aprovado, a consulta roda de verdade; exceção no preflight bloqueia com mensagem sanitizada; `_resolve_shopee_batch_window_after_preflight` confirmado fora de `__all__`; CLI usa só a função pública e nunca chama `run_window_preflight`/`psycopg2.connect` por conta própria; data futura e janela >180 dias resultam ambas em `refresh_window_invalid` (nunca `window_exceeds_limit`); JSON/exit codes coerentes; `test_validate_ok_dedup_e_ordena` renomeado para `test_validate_ok_ordena_sem_alterar_valores` (duplicados são rejeitados, não deduplicados). Suíte completa: **1393 testes**, sem regressão nos 8 arquivos focais de Gold regional (437 testes).

**Status**: implementado e testado, **zero conexão com banco real**, zero alteração em `loader.py`/`window_write_conn.py`/SQL auditado. Não commitado — gate de revisão.

### Gate S5.3 — wrapper operacional único para refresh Shopee por lote (2026-07-21)

**Implementado, testado, zero conexão com banco real, zero alteração de `loader.py`/`window_write_conn.py`/`shopee_batch_window.py`/SQL auditado.** Novo módulo: `pipelines/ops/refresh_shopee_window_if_needed.py`. Este módulo não tem **nenhum** SQL próprio nem lógica de transação — é inteiramente uma composição de três funções já auditadas: `resolve_shopee_batch_window` (S5.2.1), `window_write_conn.run_window_preflight` (S3.1) e `loader.execute_shopee_window_refresh` (S3).

**Contrato**: `refresh_shopee_window_if_needed(write_url: str, datamart_read_url: str, file_ids: Sequence[int], audit_path: Path, *, repo_root: Path = REPO_ROOT) -> ShopeeWindowRefreshIfNeededResult`. Único contrato público do módulo (`__all__` explícito) — não há divisão público/privado como no S5.2.1 porque não há necessidade: esta é a única função do módulo e sempre executa os dois preflights, sem nenhum parâmetro capaz de pular qualquer um.

**Ordem obrigatória**: (1) valida `file_ids` (`shopee_batch_window.validate_batch_file_ids`, reaproveitada) e `audit_path` (`loader._validate_new_window_audit_path`, reaproveitada — mesmas regras do refresh: absoluto, `.json`, fora do repositório, destino e sidecar `.sha256` ainda não existentes) ANTES de qualquer I/O; (2) `resolve_shopee_batch_window(write_url, datamart_read_url, file_ids)` — já exige o **primeiro preflight** e primary confirmado internamente; (3) se o outcome não for `resolved`, retorna `blocked`/`failed` de imediato preservando `reason_code`/`missing_file_ids`/`problems`/`warnings` — **o refresh nunca é chamado**; (4) `date_from`/`date_to` só são lidos do resultado `resolved`; (5) roda `window_write_conn.run_window_preflight` de novo — o **segundo preflight**, imediatamente antes do refresh, protegendo a operação de escrita o mais perto possível da transação real (`report.ok is not True` bloqueia, "inconclusivo" nunca equivale a aprovado); (6) chama `execute_shopee_window_refresh` **exatamente uma vez**, sem retry; (7) mapeia `committed`/`no_op`/`blocked`/`failed` do refresh sem reinterpretar métricas.

**Por que dois preflights**: o primeiro (dentro de `resolve_shopee_batch_window`) protege a resolução — leitura da Silver. O segundo (deste wrapper) protege a operação de escrita — entre os dois, a resolução inteira roda (uma consulta à Silver), e um privilégio pode ter sido revogado nesse intervalo; rodar de novo o mesmo preflight, o mais perto possível do `execute_shopee_window_refresh`, é deliberado, não redundância acidental.

**`committed` vs. `no_op`**: ambos são outcomes de sucesso do refresh — a diferença é só se a fonte (Silver transformada) diverge da Gold atual na janela. `committed`: houve `DELETE`+`INSERT`, com backup atômico publicado ANTES do `DELETE` (`backup_path`/`backup_sha256` preenchidos). `no_op`: Gold e fonte já batem chave a chave e campo a campo — nenhuma escrita, nenhum backup, nenhum `.sha256` (confirmado em teste: `audit_path` nunca é criado no caminho `no_op`). Esta decisão é feita EXCLUSIVAMENTE por `execute_shopee_window_refresh`, que recalcula o key-diff sob o lock, contra o primary — este wrapper nunca chama `diagnose_shopee_window` (réplica) nem usa qualquer leitura anterior para decidir `no_op`.

**`staging_rows`/`gold_rows_before`**: presentes no contrato de saída (pedidos explicitamente), mas SEMPRE `None` neste gate — `ShopeeWindowRefreshResult` não expõe essas contagens (só `rows_deleted`/`rows_inserted`, o resultado da operação, não o estado anterior), e calculá-las exigiria SQL novo ou reaproveitar `diagnose_shopee_window`/`validate_shopee_window_write_path` — nenhuma das duas opções está no escopo deste gate. Documentado no docstring do módulo e coberto por teste dedicado.

**Vocabulário de reason_code**: reaproveita, com o MESMO nome, os reason_codes de `shopee_batch_window` quando a causa é idêntica (`missing_file_ids`, `empty_batch`, `null_order_date`, `refresh_window_invalid`, `preflight_blocked`); cria nomes novos só para conceitos exclusivos deste wrapper (`audit_path_invalid`, `refresh_blocked`, `refresh_failed`). Tabela fixa `reason_code → exit code`: `0` `committed`/`no_op`; `2` `invalid_input`/`audit_path_invalid`/`datamart_url_not_configured`/`secret_load_error`/`preflight_blocked`; `3` `missing_file_ids`/`empty_batch`/`null_order_date`/`refresh_window_invalid`/`refresh_blocked`; `4` `refresh_failed`/`unexpected_error`.

**CLI**: `python -m pipelines.ops.refresh_shopee_window_if_needed --file-id <id> [--file-id <id> ...] --audit-path <caminho-absoluto.json> [--json]`. Mesma ordem de validação fail-fast do S5.2 (file_ids → audit_path → `DATAMART_DATABASE_URL` → secret dedicado `.env.gold-window-write.local` → guardrails → função pública). Com `--json`, stdout recebe um único documento JSON; `Decimal` (GMV) serializado sempre como string, `date` sempre ISO-8601 — mesma convenção já usada no formato de backup do refresh. `audit_path`/`backup_path` aparecem no JSON (evidência operacional fornecida pelo chamador); nunca conteúdo do backup, nunca host/URL/usuário/caminho de secret.

**Confirmação de zero SQL/transação própria**: nenhum `import psycopg2`; nenhum `cursor()`/`cur.execute()`/`conn.commit()`; nenhuma referência a `LOCK TABLE`/`pg_try_advisory_lock`; nenhuma chamada a `diagnose_shopee_window`/`execute_shopee_window_restore`/`sync_region_if_needed`/`sync_region_daily`; nenhuma referência a `_resolve_shopee_batch_window_after_preflight` (privada do S5.2) — só a função pública `resolve_shopee_batch_window` (testes de regressão estática cobrem todos os pontos, escaneando só o corpo das funções, nunca o docstring do módulo — mesma lição do S5.2 sobre falso positivo). A automação externa continua sem `DATABASE_URL` do Neon.

**Testes**: 45 novos em `pipelines/tests/test_ops_refresh_shopee_window_if_needed.py` — resolver blocked/failed nunca chama refresh (todos os reason_codes); resolved dispara o segundo preflight (contagem de chamadas); segundo preflight bloqueado/inconclusivo/exceção bloqueia sanitizado sem chamar refresh; refresh chamado exatamente uma vez para no_op/committed/blocked/failed, com mapeamento fiel (nunca reinterpretado); exceção inesperada do refresh vira `failed` sanitizado; `staging_rows`/`gold_rows_before` sempre `None`; write_url/read_url nunca se confundem (ordem dos argumentos verificada em cada chamada); teste de integração com `resolve_shopee_batch_window` rodando de verdade (psycopg2 falso) provando as DUAS chamadas de preflight; regras de `audit_path` (relativo, dentro do repo, extensão errada, destino/sha existentes) bloqueiam antes de qualquer resolução; `no_op` nunca cria arquivo; regressão estática tripla (nenhum símbolo proibido, só a função pública do S5.2, nunca `DATABASE_URL` do Neon); CLI com JSON único parseável, exit codes corretos, stderr só com avisos/preflight sanitizados, sem PII/infraestrutura. Suíte completa: **1438 testes**, sem regressão nos 9 arquivos focais de Gold regional/refresh/preflight/ops (582 testes).

**Status**: implementado e testado, **zero conexão com banco real**, **wrapper real nunca executado**. Não implementa ainda geração automática de `run_id`/receipt/`audit_path` (Gate S5.4, `audit_path` continua obrigatório e fornecido pelo chamador) nem `--confirm-empty-window` (não exposto neste wrapper — o caso "fonte zero + Gold com linhas" permanece `blocked`, exigindo intervenção direta via `--refresh-shopee-window` se necessário). Não commitado — gate de revisão.

### Gate S5.3.1 — hardening final do wrapper antes do commit (2026-07-21)

Revisão pré-commit encontrou quatro pontos no wrapper do S5.3, todos corrigidos dentro de `refresh_shopee_window_if_needed`, sem tocar `loader.py`/`window_write_conn.py`/`shopee_batch_window.py` e sem SQL novo:

1. **Barreira para exceção do resolvedor.** `resolve_shopee_batch_window(...)` passou a rodar dentro de um `try/except` próprio — antes desta correção, uma exceção nativa ali escaparia da função pública inteira (nenhum outro bloco a envolvia). Agora vira `failed`/`unexpected_error` sanitizado, e nem o segundo preflight nem o refresh chegam a ser chamados. Testado com chamada DIRETA da API pública (não só via `run_cli`).
2. **Contrato de `resolved` validado explicitamente.** `outcome == "resolved"` deixou de ser suficiente por si só: `_resolved_contract_problems` confirma, antes de extrair `date_from`/`date_to`, que `reason_code == resolved`; `date_from`/`date_to` são `date` de verdade e em ordem; `refresh_window_valid is True`; `requested_file_count == found_file_count`; `missing_file_ids` vazio; `window_days` é inteiro positivo. Qualquer invariante violada bloqueia como **`failed`/`resolver_contract_invalid`** (novo reason_code, exit 4) — nunca corrige/infere um valor, e nem o segundo preflight nem o refresh rodam. Testado com 8 variações de contrato quebrado (datas ausentes/invertidas, `refresh_window_valid=False`, `missing_file_ids` presente, contagens divergentes, `window_days` zero/`None`, `reason_code` inconsistente) + contraprova de que um `resolved` válido não é afetado.
3. **Warnings agregados das três etapas.** O resultado final agora agrega, na ordem em que as etapas efetivamente rodaram e sem duplicar entradas idênticas (deduplicação estável, preserva a primeira ocorrência): `resolve_result.warnings` sempre; `second_report.warnings` a partir do momento em que o segundo preflight retorna (mesmo bloqueado); `refresh_result.warnings` só se o refresh chegou a ser chamado. Um warning nunca vira sucesso nem falha — é só telemetria preservada. Testado para `no_op`/`committed` (warnings das três etapas presentes, na ordem) e para segundo preflight bloqueado/levantando exceção (warnings do resolvedor preservados; do report, quando existir).
4. **Parser de CLI deixou de importar símbolo privado do S5.2.** `run_cli` chamava `shopee_batch_window._parse_cli_file_ids` (privada, prefixo `_`, nunca exportada por aquele módulo). Substituído por um parser LOCAL mínimo (`_parse_cli_file_ids`, definido neste módulo) que só converte string→int e delega toda a validação de fato para a função pública `shopee_batch_window.validate_batch_file_ids` — `shopee_batch_window.py` não foi alterado. Regressão estática confirma a ausência do padrão de CHAMADA (`shopee_batch_window._parse_cli_file_ids(`/`_resolve_shopee_batch_window_after_preflight(`) — não apenas da string, já que o docstring do módulo cita esses nomes em prosa ao explicar o que nunca é feito.

Preservado integralmente: dois preflights; zero `diagnose_shopee_window`; refresh no máximo uma vez; `confirm_empty_window` não exposto; `audit_path` obrigatório; zero SQL/transação/retry/sync/restore no wrapper; JSON único e exit codes existentes (só `resolver_contract_invalid` foi adicionado à tabela, mapeado para exit 4).

**Testes**: suíte do wrapper cresceu de 45 para **66** testes. Suíte completa: **1459 testes**, sem regressão nos 9 arquivos focais de Gold regional/refresh/preflight/ops (603 testes).

**Status**: implementado e testado, **zero conexão com banco real**, **wrapper real nunca executado**. Não commitado — gate de revisão.

### Gate S5.4a — desenho do contrato de evidência operacional (2026-07-22)

**Somente diagnóstico/desenho — zero código alterado, zero banco real, zero artefato criado fora de `tmp_path`.** Inspecionados: `pipelines/ops/refresh_shopee_window_if_needed.py` (S5.3/S5.3.1, contrato congelado), `loader.py` (`_write_window_backup_atomic`, `_validate_new_window_audit_path`, `_sha256_file`, `WINDOW_BACKUP_SCHEMA_VERSION`), `pipelines/ingestion/shopee_raw/backfill_ads_metadata.py` (mesmo padrão `mkstemp`+`fsync`+`os.link`, reimplementado ali de propósito — não importado — por separação de domínio), `sync_region_daily.py` (`_now_tag`/`_validate_identifier`, precedente de tag temporal e de allowlist regex), `sync_produtos.py`/`daily_performance.py` (`sync_run_id` — um id **de banco**, gerado por `RETURNING` numa tabela de auditoria própria; **não é o mesmo conceito** de `run_id` deste gate, que é puramente local/filesystem e nunca toca essa tabela). Nenhum precedente de `run_id`/`receipt`/`schema_version` de arquivo já existe para este caminho — este gate desenha do zero.

**Pergunta central e a decisão que a resolve**: como garantir que uma falha do receipt depois de um `commit` da Gold nunca faça a automação interpretar que o refresh não aconteceu? Resposta: **`operation_outcome` e `receipt_status` são dois campos independentes, nunca combinados em um só**. `operation_outcome` (`committed`/`no_op`/`blocked`/`failed`) continua vindo EXCLUSIVAMENTE de `execute_shopee_window_refresh` via `refresh_shopee_window_if_needed` — a escrita/falha do receipt NUNCA o altera, nunca o esconde, nunca o reinterpreta. `receipt_status` (`ok`/`failed`/`not_attempted`) descreve só a evidência local. A automação deve decidir "os dados mudaram?" lendo `operation_outcome`, nunca `receipt_status`. O único lugar onde isso ganha um sinal adicional é o exit code: `committed` + receipt falho ganha um exit code PRÓPRIO (5, não 4) — ver Matriz abaixo — para que mesmo uma automação que só olha exit code (sem parsear o JSON) perceba que precisa investigar, sem jamais confundir com "o refresh falhou/rollback".

#### 1. Identificadores

- **`batch_id`**: string opaca fornecida pela automação externa (Raw). Nunca validada contra o banco — reafirma a decisão já registrada no docstring do S5.2 ("este módulo nunca lê `raw.shopee_ingestion_file` nem tenta confirmar `batch_id`"). Só validada por FORMATO (ver regex abaixo). No receipt, `batch_id` é metadado de correlação fornecido pelo chamador — a mera presença do campo nunca deve ser lida como "validado no banco pela role Gold"; nenhum campo tipo `batch_id_verified` é criado, e o docstring do módulo novo deixa isso explícito por escrito.
- **`run_id`**: **os dois** — `--run-id` é OPCIONAL. Se a DAG fornecer, usa como está (só valida formato) — permite correlacionar o receipt com o run_id nativo da própria orquestração. Se omitido (uso manual, ad-hoc), gera localmente: `{timestamp_utc_compacto}-{8 hex}` (ex.: `20260722T143015Z-a1b2c3d4`, via `datetime.now(timezone.utc)` + `uuid.uuid4().hex[:8]`) — ordenável lexicograficamente, resistente a colisão sem precisar de contador/lock file. Mesmo espírito de `_now_tag()` (`sync_region_daily.py`), com sufixo aleatório a mais porque aqui o run_id vira nome de arquivo compartilhado por um diretório potencialmente usado por múltiplas execuções concorrentes.
- **Formato/comprimento/path traversal**: um único validador (`_validate_run_or_batch_id`) reaproveitado para os dois campos: string não vazia, `len <= 128`, regex `^[A-Za-z0-9][A-Za-z0-9_.-]*$` (não pode começar com `.`/`-`), e rejeição explícita de `..` como substring (defesa em profundidade mesmo a regex já não permitindo `/`). Nunca concatenado cru em um path — sempre via `Path(dir) / nome_montado`.
- **Nova tentativa manual — reutilizar ou criar outro run_id?** Nenhuma lógica especial de "contador de tentativa" é necessária: a garantia "nunca sobrescrever" (mesmo mecanismo `os.link`/`FileExistsError` do backup) já resolve isso de graça. Se a tentativa anterior não chegou a publicar nada em disco (bloqueio de entrada/config, preflight bloqueado antes de qualquer resolução), reusar o mesmo `run_id` é seguro — não há nada para colidir. Se a tentativa anterior já publicou QUALQUER artefato (backup e/ou receipt) para aquele `(batch_id, run_id)`, reusar o mesmo `run_id` é automaticamente rejeitado pela checagem "já existe" (Matriz, item A/H) — o que força, corretamente, uma nova tentativa manual a vir com um `run_id` novo, sem precisar de uma regra adicional.

#### 2. Versionamento

- **`git_commit`**: obtido via `git rev-parse HEAD`, reaproveitando o helper já auditado `write_conn._run_git` (mesmo padrão de subprocess já usado por `window_write_conn.load_window_write_secret`) — nunca uma nova implementação de subprocess. Somente leitura, local, sem rede, sem tocar nenhuma credencial Postgres.
- **`.git` ausente ou binário `git` não encontrado**: `git_commit = null` + **warning** (nunca bloqueia): `"git_commit indisponível: não foi possível determinar (repositório .git ausente ou git não encontrado)"`. Decisão: git é metadado de proveniência/auditoria, não um gate de segurança como o preflight — bloquear uma operação real da Gold por causa de um ambiente de deploy sem `.git` seria desproporcional.
- **Working tree dirty**: `git status --porcelain` (ou `git diff --quiet` + `git diff --cached --quiet`) → `git_dirty: true|false|null` (`null` se a checagem em si falhar) + warning quando `true`: `"working tree possui alterações não commitadas — git_commit pode não refletir exatamente o código em execução"`. Nunca bloqueia.
- **`DESIGN.md`/docs pré-existentes não geram exceção especial**: a checagem de dirty é genérica (`git status --porcelain` cru) — nenhuma lista de arquivos ignorados, nenhum tratamento especial para os três arquivos historicamente fora de escopo deste projeto. Se estiverem modificados no momento da execução, `git_dirty=true` é reportado com honestidade (é warning, não bloqueio) — não há necessidade de "decisão explícita" adicional além desta: a checagem sempre diz a verdade e nunca impede a operação.
- **Decisão final**: `git_commit`/`git_dirty` ausentes ou indisponíveis **nunca bloqueiam** — sempre `warning`, nunca `problem`, nunca reason_code.

#### 2b. Diretório e nomes (convenção proposta)

Um único `--artifacts-dir <dir>` absoluto, validado (absoluto, fora do repositório, é diretório, já existe, gravável — via probe de escrita, ver Matriz item B). Dentro dele, três arquivos por execução, nomeados deterministicamente a partir de `batch_id`+`run_id` (já validados pela regex acima, portanto seguros para interpolar em nome de arquivo):

```
shopee_window_backup_{batch_id}_{run_id}.json
shopee_window_backup_{batch_id}_{run_id}.json.sha256
shopee_window_receipt_{batch_id}_{run_id}.json
```

Satisfaz: nenhum dado sensível no nome (`batch_id`/`run_id` são ids opacos de correlação, nunca order_id/filename original do scraping); `.sha256` no MESMO padrão já usado pelo backup (`str(path) + ".sha256"`); nunca sobrescreve (`os.link`, `FileExistsError` se já existir — mesmo mecanismo do backup, estendido ao receipt).

#### 3. Publicação do receipt

Mesmo mecanismo de 6 passos já auditado em `_write_window_backup_atomic` (reimplementado localmente no módulo novo — não importado de `loader.py`, mesma separação de domínio já usada por `backfill_ads_metadata.py`): `tempfile.mkstemp` no MESMO diretório → `write`+`flush`+`os.fsync` → `os.link(tmp, receipt_path)` (falha com `FileExistsError` se já existir — nunca sobrescreve) → `tmp` removido em `finally` SEMPRE → releitura do disco + revalidação estrutural do JSON antes de declarar `receipt_status="ok"` (um arquivo parcial nunca vira receipt válido: a escrita só toca o nome final via `os.link`, nunca escreve diretamente nele). Nenhuma exclusão do backup em nenhum caminho, sucesso ou falha — backup e receipt são permanentes do ponto de vista desta execução (ver Retenção). `schema_version` (inteiro, mesmo padrão de `WINDOW_BACKUP_SCHEMA_VERSION=1`) é o primeiro campo do receipt.

#### 4. Matriz crítica de falhas

| # | Cenário | `operation_outcome` | `receipt_status` | `reason_code` | Exit |
|---|---|---|---|---|---|
| A | Diretório/paths inválidos (relativo, dentro do repo, não existe, ou qualquer um dos 3 nomes computados já existe) — antes do banco | `blocked` | `not_attempted` | `artifacts_dir_invalid` | **2** |
| B | Probe de escrita no diretório falha (permissão negada) — antes do refresh | `blocked` | `not_attempted` | `artifacts_dir_not_writable` | **2** |
| C | Refresh `blocked`/`failed`, receipt publicado com sucesso | `blocked`/`failed` | `ok` | (o mesmo reason_code já existente do S5.3/S5.3.1) | 3/4 (inalterado) |
| D | Refresh `no_op`, receipt publicado | `no_op` | `ok` | `no_op` | **0** |
| E | Refresh `committed`, receipt publicado | `committed` | `ok` | `committed` | **0** |
| F | Refresh `committed`, publicação do receipt FALHA | **`committed`** (nunca alterado/escondido) | `failed` | `committed_receipt_publish_failed` (novo, exclusivo desta combinação) | **5 (novo)** |
| G | Backup publicado, refresh falha/rollback, receipt também falha | `failed` | `failed` | `refresh_failed`/`unexpected_error` (inalterado — já sinaliza "nada persistido") | 4 (inalterado) |
| H | Path do receipt já existe para o mesmo `run_id` | detectado cedo → como A (exit 2); detectado só na publicação (corrida) → como F/G conforme `operation_outcome` | `not_attempted` ou `failed` | `artifacts_dir_invalid` (cedo) ou o mesmo de F/G (tarde) | 2, ou 5/4 conforme o caso |

Regra geral: só `committed` ganha o exit code diferenciado (5) quando o receipt falha — `no_op`/`blocked`/`failed` já têm exit codes que não sugerem "sucesso silencioso" (0 para `no_op` é seguro porque nada foi escrito na Gold; `blocked`/`failed` já são 2/3/4). `committed` é o único caso em que o exit code padrão (0) esconderia uma lacuna real de evidência — daí o novo código.

**Semântica do exit 5**: "operação de dados concluída com sucesso; evidência local (receipt) incompleta — requer conferência/reconstituição manual a partir de `backup_path`/`backup_sha256` (já publicados pelo próprio refresh, antes do DELETE); nunca retry automático do refresh." O JSON no caso F preserva TODOS os campos normais do resultado (`rows_deleted`, `rows_inserted`, `gmv_before`/`gmv_after`, `backup_path`, `backup_sha256`) exatamente como um `committed` bem-sucedido — só `receipt_status`/`reason_code`/exit code sinalizam a lacuna.

**Correções aplicadas na implementação (Gate S5.4b)** — três pontos deste desenho ficaram incompletos ou imprecisos e foram corrigidos ao implementar:
1. **`run_id` NÃO é autogerado.** A frase "os dois — DAG-supplied OU gerado localmente" (seção 1 acima) estava errada: `batch_id` e `run_id` são **sempre obrigatórios**, fornecidos pelo chamador (a DAG) — este módulo nunca gera nenhum dos dois. Uma nova tentativa manual precisa vir com um `run_id` novo; isso é responsabilidade de quem chama, não deste módulo (a garantia "nunca sobrescrever" já torna perigoso reusar um `run_id` cuja tentativa anterior tenha publicado qualquer artefato — ver seção 1 acima, que permanece correta nesse ponto).
2. **`no_op` + receipt falho TAMBÉM usa exit 5**, não só `committed`. A matriz acima (linha F) só cobria `committed`; a regra correta e implementada é: qualquer `operation_outcome` cujo exit code BASE seria 0 (`committed` OU `no_op`) escala para 5 quando o receipt falha — porque em ambos os casos o exit 0 padrão esconderia silenciosamente a lacuna de evidência. `blocked`/`failed` (linhas C/G) NUNCA escalam, porque seus exit codes (2/3/4) já não sugerem sucesso.
3. **Nomes de arquivo**: a convenção com underscores (`shopee_window_backup_{batch_id}_{run_id}.json` etc., seção 2b acima) está correta e foi implementada sem alteração — confirmado aqui só para deixar explícito que não há divergência entre o desenho e o código.

#### 5. Schema do receipt (proposto)

```json
{
  "schema_version": 1,
  "batch_id": "string opaca, fornecida pelo chamador",
  "run_id": "string opaca, fornecida ou gerada localmente",
  "file_ids": [1, 2, 3],
  "started_at_utc": "2026-07-22T14:30:00Z",
  "finished_at_utc": "2026-07-22T14:30:07Z",
  "duration_seconds": 7.42,
  "git_commit": "abc123...ou null",
  "git_dirty": true,
  "operation_outcome": "committed",
  "reason_code": "committed",
  "date_from": "2026-06-15",
  "date_to": "2026-06-21",
  "window_days": 7,
  "requested_file_count": 3,
  "found_file_count": 3,
  "silver_row_count": 512,
  "rows_deleted": 10,
  "rows_inserted": 12,
  "gmv_before": "500.00",
  "gmv_after": "620.00",
  "backup_path": "/abs/path/...",
  "backup_sha256": "...",
  "receipt_status": "ok",
  "problems": [],
  "warnings": []
}
```
Mapeia 1:1 os campos já existentes de `ShopeeWindowRefreshIfNeededResult` (S5.3/S5.3.1, inalterado) + identificadores/timestamps/metadado de versão de código/`receipt_status`, únicos campos genuinamente novos. Decimal sempre como string, data sempre ISO-8601 (mesma convenção do backup). NUNCA inclui: URL/host/IP/usuário, conteúdo de secret, `order_id`, filename original do scraping, linhas individuais, conteúdo do backup (só `backup_path`/`backup_sha256`, nunca `before_records`/`planned_after_records`).

#### 6. Integração no código — decisão arquitetural

Comparadas 3 opções: (A) ampliar `refresh_shopee_window_if_needed.py`; (B) módulo novo `pipelines/ops/run_shopee_gold_batch.py`, que CHAMA `refresh_shopee_window_if_needed` sem alterá-lo; (C) receipt dentro de `loader.py` — descartada de imediato (violaria a restrição permanente de não alterar `loader.py` e misturaria "operação autoritativa de banco" com "evidência operacional/orquestração").

**Recomendação: opção B.** Motivos: (1) preserva o contrato S5.3/S5.3.1 já versionado e testado (66 testes) sem nenhum risco de regressão — zero linha alterada em `refresh_shopee_window_if_needed.py`; (2) `refresh_shopee_window_if_needed` continua reutilizável sozinho, com a assinatura exatamente como está hoje; (3) não duplica a LÓGICA de secret/preflight — o módulo novo chama as MESMAS funções já auditadas (`window_write_conn.load_window_write_secret`/`validate_window_write_guardrails`/`run_window_preflight` só indiretamente, via `refresh_shopee_window_if_needed`) exatamente como `shopee_batch_window.run_cli` e `refresh_shopee_window_if_needed.run_cli` já fazem cada um independentemente hoje (mesmo padrão aceito de pequena duplicação de boilerplate de CLI, nunca de algoritmo); (4) testes determinísticos: a suíte nova trata `refresh_shopee_window_if_needed` como caixa-preta (monkeypatch), sem precisar reimplementar nenhum fake de banco.

#### 7. Compatibilidade

CLI atual (`python -m pipelines.ops.refresh_shopee_window_if_needed --file-id ... --audit-path ... [--json]`) permanece **exatamente como está** — módulo intocado. Nova CLI, em módulo novo e separado: `python -m pipelines.ops.run_shopee_gold_batch --file-id <id> [--file-id <id> ...] --artifacts-dir <dir-absoluto> --batch-id <id> [--run-id <id>] [--json]` — `--artifacts-dir`/`--batch-id` obrigatórios, `--run-id` opcional (gerado se omitido). "Mutuamente exclusivo" é resolvido pela SEPARAÇÃO EM DOIS PROGRAMAS/MÓDULOS distintos (não por um único parser com grupos condicionais) — evita qualquer ambiguidade de precedência entre `--audit-path` e `--artifacts-dir` num mesmo comando. Contrato S5.3 não é tocado.

#### 8. Retenção

Nenhuma deleção automática neste gate nem em nenhum futuro Gate S5.4b — backups e receipts são permanentes do ponto de vista da execução que os cria. Política de retenção recomendada para o FUTURO: um job separado, read-only por padrão, com seu próprio gate de consentimento explícito (mesmo padrão `.env.gold-window-write.local`) — nunca invocado implicitamente pelo caminho de refresh/receipt.

#### 9. Arquivos que mudariam no Gate S5.4b

- NOVO: `pipelines/ops/run_shopee_gold_batch.py`
- NOVO: `pipelines/tests/test_ops_run_shopee_gold_batch.py`
- MODIFICADO: `docs/regional_design_draft.md` (seção S5.4b)
- MODIFICADO: `docs/shopee_datamart_operacao_completa.md` (bullet S5.4b)
- **Sem alteração**: `loader.py`, `window_write_conn.py`, `shopee_batch_window.py`, `refresh_shopee_window_if_needed.py` (S5.3/S5.3.1, congelado), `pipelines/ops/__init__.py` (já existe vazio).

#### 10. Testes obrigatórios (S5.4b)

Validação de `run_id`/`batch_id` (formato, comprimento, `..`, vazio); `artifacts_dir` inválido/relativo/dentro do repo/inexistente bloqueia antes do banco (Matriz A); probe de escrita falha bloqueia antes do refresh (Matriz B); `refresh_shopee_window_if_needed` mockado retornando cada `operation_outcome` — receipt publicado com sucesso preserva todos os campos (C/D/E); publicação do receipt falhando com `operation_outcome=committed` produz exit 5 e preserva `backup_path`/`backup_sha256`/`rows_deleted`/`rows_inserted` (F); publicação falhando com `operation_outcome=failed` mantém exit 4 (G); path do receipt já existente bloqueia cedo (H, exit 2) e simulação de corrida no `os.link` (`FileExistsError` armadilhado) bloqueia tarde com o mesmo `operation_outcome` preservado; `git_commit`/`git_dirty` ausentes viram warning, nunca bloqueiam (testado com `_run_git` falso retornando erro); nenhuma exclusão de backup em nenhum teste; regressão estática (zero SQL novo, zero chamada a `diagnose_shopee_window`/restore/sync, `refresh_shopee_window_if_needed` chamado como caixa-preta); JSON único, exit codes fechados incluindo o novo 5; CLI nova com `--artifacts-dir`/`--batch-id`/`--run-id`; scan de PII/segredos no schema do receipt.

#### 11. Riscos restantes

Corrida TOCTOU residual entre a checagem antecipada (Matriz A) e a publicação real (mesma classe de risco já aceita para `audit_path` desde o S3 — mitigada, nunca eliminada, pelo `os.link`/`FileExistsError`; a segurança dos dados em si continua garantida pelo lock advisory no Postgres, não por este check local). `run_id`, sendo sempre fornecido pela DAG (nunca autogerado — ver correção acima), não é formalmente livre de colisão só porque este módulo não gera nada: cabe à DAG garantir unicidade por tentativa; a garantia "nunca sobrescrever" é a rede de segurança final. Disco cheio/quota durante a escrita do receipt cai no mesmo caminho já desenhado (`receipt_status=failed`, exit 4/5 conforme `operation_outcome`) — sem tratamento especial necessário. Risco operacional a documentar explicitamente para quem consumir o exit code 5: a política de retry da automação PRECISA tratar o exit 5 como "não repetir o refresh — investigar/reconciliar o receipt a partir do `backup_path` já publicado", nunca como um exit genérico de falha a ser re-tentado. Ambiente de deploy sem `.git`/binário `git` deve ser validado antes do S5.4b (warning-only já decidido, mas vale confirmar que o binário `git` está disponível no runtime real). Reuso indevido de `(batch_id, run_id)` pela automação (bug na DAG, não neste módulo) é mascarado como bloqueio "já existe" — seguro, mas pode confundir quem depura; documentado como comportamento esperado, não defeito.

### Gate S5.4b — implementação do job Shopee com artifacts e receipt atômico (2026-07-22)

**Implementado, testado, zero conexão com banco real, zero alteração de `loader.py`/`window_write_conn.py`/`shopee_batch_window.py`/`refresh_shopee_window_if_needed.py`.** Novo módulo: `pipelines/ops/run_shopee_gold_batch.py`. Este módulo não tem SQL próprio, não abre conexão — a ÚNICA operação de dados que chama é `refresh_shopee_window_if_needed` (S5.3/S5.3.1), inalterado.

**Contrato**: `run_shopee_gold_batch(write_url, datamart_read_url, file_ids, artifacts_dir, batch_id, run_id, *, repo_root=REPO_ROOT) -> ShopeeGoldBatchResult`. `batch_id`/`run_id` **sempre obrigatórios** (corrige a ambiguidade do S5.4a) — nunca gerados automaticamente.

**Ordem**: valida `batch_id`/`run_id` (allowlist ASCII `^[A-Za-z0-9][A-Za-z0-9._-]*$`, ≤100 caracteres, `..` explicitamente rejeitado — Unicode/espaço/`/`/`\` já rejeitados pelo simples não-casamento da regex) → valida `artifacts_dir` (absoluto, fora do repositório, existe, é diretório) → computa os 3 nomes determinísticos e recusa se QUALQUER um já existir → probe de escrita (cria+fsync+remove um temporário; falha na criação OU na remoção bloqueia, nunca deixa resíduo) → SÓ ENTÃO chama `refresh_shopee_window_if_needed(write_url, datamart_read_url, file_ids, backup_path)` → monta e publica o receipt atomicamente (mesmo padrão técnico de `_write_window_backup_atomic`, reimplementado localmente — nunca importado, schema diferente) → `operation_outcome`/`receipt_status` permanecem sempre dois campos independentes.

**Vocabulário de reason_code**: locais deste módulo (`invalid_input`, `artifacts_dir_invalid`, `artifacts_dir_not_writable`, `datamart_url_not_configured`, `secret_load_error`, `unexpected_error`) todos exit 2 (exceto `unexpected_error`, exit 4) — nenhum chega perto do banco. Reason_codes vindos de `refresh_shopee_window_if_needed` são reaproveitados VERBATIM (nunca reinventados) via `_BASE_EXIT_CODE = {**_LOCAL_REASON_EXIT_CODE, **refresh_wrapper._REASON_CODE_EXIT_CODE}` — fonte única, sem risco de drift entre as duas tabelas.

**Exit code final** (`_exit_code_for`): nunca deriva só de `reason_code`, porque a MESMA operação pode ter dois exit codes diferentes dependendo de `receipt_status`. Regra: `base = _BASE_EXIT_CODE[reason_code]`; se `receipt_status == "failed"` E `operation_outcome` em `("committed", "no_op")`, o exit vira **5**; caso contrário, o `base` é preservado sem alteração.

| `operation_outcome` | `receipt_status` | Exit |
|---|---|---|
| validação local falhou | `not_attempted` | 2 |
| `blocked` | `ok` | 3 (ou 2, se o reason_code for `preflight_blocked`/`invalid_input`) |
| `blocked` | `failed` | **inalterado** — 3 (ou 2) |
| `failed` | `ok` | 4 |
| `failed` | `failed` | **inalterado** — 4 |
| `no_op` | `ok` | 0 |
| `committed` | `ok` | 0 |
| `no_op` | `failed` | **5** |
| `committed` | `failed` | **5** |

**Prova de que `committed`/`no_op` nunca viram `failed`**: `run_shopee_gold_batch` monta o `ShopeeGoldBatchResult` final SEMPRE a partir de `refresh_result.outcome`/`refresh_result.reason_code` (nunca reatribuídos); a publicação do receipt só popula `receipt_status`/anexa a `problems`/`warnings` — nunca escreve em `operation_outcome`. Testes dedicados (`test_committed_receipt_falha_nunca_vira_failed`, `test_no_op_receipt_falha_nunca_vira_failed`) mockam `_publish_receipt_atomic` para falhar e confirmam `operation_outcome` intacto + `rows_deleted`/`rows_inserted`/`backup_path`/`backup_sha256` preservados.

**Git**: `subprocess.run(["git", *args], cwd=..., capture_output=True, text=True, timeout=5, shell=False)` — lista de argumentos (nunca `shell=True`), timeout curto, nunca imprime `stderr` nativo (só `returncode`/`stdout.strip()` para o hash, ou `type(exc).__name__` para qualquer falha). `.git` ausente/binário não encontrado/timeout viram `warning`, nunca bloqueiam. `git_dirty` é só um booleano (nunca a lista de arquivos — confirmado em teste que a saída de `git status --porcelain` nunca aparece no JSON). Clock (`_utc_now`) e coletor Git (`_run_git_subprocess`) são funções soltas no nível do módulo, monkeypatcháveis diretamente nos testes — sem parâmetro de injeção na assinatura pública, sem framework genérico.

**Receipt**: publicação atômica reimplementada localmente (`_publish_receipt_atomic`) — `mkstemp` no mesmo diretório → `write`+`flush`+`fsync` → `os.link` exclusivo (nunca sobrescreve; `FileExistsError` é a proteção FINAL contra corrida, não o `exists()` antecipado) → releitura+revalidação estrutural → `tmp` removido em `finally` sempre; falha só na limpeza do `tmp` NUNCA mascara o sucesso da publicação (vira `warning`, não `problem`, `receipt_status` continua `"ok"`). Nunca reutiliza `loader._write_window_backup_atomic` (payload completamente diferente) — só o padrão técnico.

**CLI**: `python -m pipelines.ops.run_shopee_gold_batch --file-id <id> [--file-id <id> ...] --artifacts-dir <dir-absoluto> --batch-id <id> --run-id <id> [--json]`. `--artifacts-dir`/`--batch-id`/`--run-id` todos obrigatórios (argparse `required=True`) — CLI do S5.3 (`--audit-path`) permanece intocada, em módulo separado.

**Testes**: 67 novos em `pipelines/tests/test_ops_run_shopee_gold_batch.py` — allowlist de ids (vazio/`..`/barra/contrabarra/espaço/Unicode/tamanho excessivo bloqueiam; válidos passam); `artifacts_dir` relativo/dentro do repo/inexistente/é-arquivo bloqueia; qualquer um dos 3 artefatos já existente bloqueia; probe falha na criação/remoção bloqueia sem resíduo; `refresh_shopee_window_if_needed` chamado exatamente uma vez com os args corretos; matriz completa outcome×receipt_status×exit code (8 combinações parametrizadas); `committed`/`no_op` + receipt falho nunca viram `failed` (dedicado); `backup_path`/`sha256` preservados quando o refresh falha depois do backup publicado; publicação atômica do receipt (nunca sobrescreve, releitura, remove temp em sucesso/falha, falha de limpeza não mascara, corrida no `os.link` bloqueia sem sobrescrever); receipt determinístico entre execuções idênticas; git commit disponível/`.git` ausente/timeout/dirty (warning-only, nunca bloqueia); timestamps UTC ISO-8601; duração nunca negativa; CLI (ordem de validação, JSON único mesmo com receipt falho, stderr só avisos/preflight, sem PII/infraestrutura); regressão estática (zero SQL/psycopg2/cursor/commit, zero chamada direta a resolvedor/preflight/refresh/diagnose/restore/sync, zero função privada do S5.2/S5.3, `shell=False`+timeout no subprocess Git). Suíte completa: **1526 testes**, sem regressão nos 10 arquivos focais de Gold regional/refresh/preflight/ops (670 testes).

**Status**: implementado e testado, **zero conexão com banco real**, **job real nunca executado**. Retenção/deleção automática não implementada (nem nesta nem em nenhuma execução futura da mesma natureza — ver seção 8 do S5.4a). Não commitado — gate de revisão.

### Gate S5.4b.1 — hardening final de validação e artefatos (2026-07-22)

Revisão pré-commit corrigiu quatro pontos no módulo do S5.4b, todos dentro de `run_shopee_gold_batch.py`, sem tocar `loader.py`/`window_write_conn.py`/`shopee_batch_window.py`/`refresh_shopee_window_if_needed.py`:

1. **`file_ids` validado explicitamente com a função pública do S5.2** (`shopee_batch_window.validate_batch_file_ids`) — antes, a validação de faixa/duplicados/limite só acontecia indiretamente, várias camadas depois, dentro de `resolve_shopee_batch_window`. Agora: a CLI converte string→int e chama `validate_batch_file_ids` ANTES de Git/`artifacts_dir`/probe/secret/conexão; `run_shopee_gold_batch` repete a MESMA chamada como defesa em profundidade, antes até de coletar Git ou tocar o filesystem. A lista já validada/ordenada (nunca a bruta) é a usada nos nomes determinísticos, na chamada ao S5.3, no receipt e no stdout. Entrada inválida (vazia, duplicada, zero, negativa, acima do bigint, acima do limite de quantidade) bloqueia como `blocked`/`invalid_input`/`receipt_status=not_attempted`/exit 2 — o refresh nunca é chamado, e nem Git nem o probe de escrita chegam a rodar (confirmado com armadilhas `AssertionError` em ambos nos testes).
2. **`git_dirty=True` agora sempre produz warning.** Antes, só a FALHA em determinar `git_dirty` gerava warning — o estado "dirty" em si era silencioso. Agora, sempre que `git_dirty is True`, um warning GENÉRICO é adicionado ("working tree possui alterações não commitadas; git_commit pode não representar integralmente o código executado") — nunca a saída de `git status`, nunca nomes de arquivo, nunca bloqueia.
3. **`_publish_receipt_atomic` reestruturado** para nunca retornar de dentro do bloco protegido por `finally` — um `return` dentro do `try` "fixa" o valor de retorno antes do `finally` rodar, então uma falha de escrita/link CONCORRENTE com uma falha de limpeza do temporário perdia silenciosamente o aviso de limpeza. Agora `publish_problem`/`cleanup_warning`/`linked_successfully` são variáveis locais preenchidas primeiro, com um único ponto de retorno após o `try`/`finally` — garantindo que as duas informações cheguem sempre juntas quando ambas existirem.
4. **Revalidação pós-publicação agora é integral** (`reread != payload`, dict completo) — antes só comparava `schema_version`/`run_id`, deixando passar despercebida qualquer divergência em outro campo (`operation_outcome`, `backup_sha256`, `file_ids`, etc.). Qualquer campo divergente vira `problem`/`receipt_status=failed`; o receipt divergente nunca é removido automaticamente — preservado como evidência para investigação manual.

Preservado integralmente (nenhuma mudança): `committed`/`no_op` + receipt falho → exit 5; `blocked`/`failed` preservam seus próprios exit codes (2/3/4); `operation_outcome` nunca alterado pela publicação do receipt; `run_id`/`batch_id` continuam obrigatórios, sem autogeração; nomes determinísticos inalterados; zero SQL/banco direto; única operação de dados é `refresh_shopee_window_if_needed`; zero retry; os quatro módulos protegidos permanecem sem diff.

**Testes**: suíte do módulo cresceu de 67 para **82** testes — validação de `file_ids` via `validate_batch_file_ids` (vazia/duplicada/zero/negativa/acima do bigint/acima do limite, todas bloqueando antes de Git/probe/refresh, tanto na função pública quanto na CLI antes do secret); `git_dirty=True` produz o warning esperado sem nenhum nome de arquivo; publicação combinando falha principal (escrita ou link) com falha de limpeza — ambas preservadas no retorno; publicação bem-sucedida com falha de limpeza retorna `problem=None` + warning; revalidação integral detectando divergência em `operation_outcome`/`backup_sha256`/`file_ids`, sempre sem remover o receipt. Suíte completa: **1541 testes**, sem regressão nos 10 arquivos focais de Gold regional/refresh/preflight/ops (685 testes).

**Status**: implementado e testado, **zero conexão com banco real**, **job real nunca executado**. Não commitado — gate de revisão.
