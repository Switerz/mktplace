# Contrato da composição de kits (BOM) no monitoramento de preços

Gate **KITS-MAP-2**. Implementado em
[`apps/api/app/services/pma_kit_bom.py`](../apps/api/app/services/pma_kit_bom.py),
travado em [`apps/api/tests/test_pma_kit_bom.py`](../apps/api/tests/test_pma_kit_bom.py).

Este documento descreve o que o monitoramento de preços **pode** publicar como
referência de kit e, principalmente, o que ele **recusa**. Nada aqui está
ligado em produção: nenhuma flag foi criada, nenhum publisher foi executado e
nenhuma migration foi aplicada.

---

## 1. Fonte da composição

Fonte canônica: **`raw.protheus_kit_components`** (Data Mart AWS, card
*Protheus Kit Components*).

`gold.bridge_kit_componente_gobeauty` é **derivada** dela — provado por `EXCEPT`
bidirecional em 2026-09-25:

| verificação | resultado |
|---|---|
| `gold − raw` | **0 pares** |
| `raw − gold` | 142 pares (63 inativos + 79 ativos descartados por ausência no `map_produto`) |
| `qty_per_kit` divergente entre os lados, nos 4.548 pares comuns | **0** |
| duplicata `KBB99170\|BB02030` | presente **nos dois lados** |

O contrato consome a `raw` porque ela é a origem e porque a `gold` já perde 79
relações ativas por um filtro de catálogo que nada tem a ver com composição.

Grão: uma linha por `(kit_sku, component_sku)`. Colunas usadas: `kit_sku`,
`component_sku`, `qty_per_kit`, `valid_from`, `valid_to`, `active`.
**Não existe coluna de marca, alias, EAN ou código de marketplace nessa tabela** —
por isso a ponte vem de outro lugar (seção 3).

## 2. Regras de consumo da BOM

`KitBomIndex.build` recusa a **carga inteira** — não a linha — nos casos abaixo.
Recusar a linha produziria um kit silenciosamente mais barato, que é o defeito
que este gate existe para impedir.

| regra | comportamento |
|---|---|
| `active = false` | linha ignorada |
| fora de vigência (`valid_from > hoje` ou `valid_to < hoje`) | linha ignorada |
| `valid_from` / `valid_to` nulos | **bordas abertas** — não limitam |
| par duplicado com quantidade **idêntica** | colapsa para uma ocorrência |
| par duplicado com quantidade **divergente** | `BomContractError`, nomeando o par |
| `qty_per_kit` nula, zero, negativa ou não finita | `BomContractError` |
| kit componente de si mesmo | `BomContractError` |
| ciclo (A→B→C→A) | `BomContractError` |
| mais de `MAX_BOM_ROWS` (200.000) linhas | `BomContractError` — recusa, nunca trunca |

**Por que bordas nulas são abertas.** 1.850 das 4.691 linhas têm `valid_from`
nulo — todas de `source = 'apice_sheet'`. Ler nulo como "não vigente"
descartaria a BOM inteira da Ápice.

**Por que a duplicata colapsa e não soma.** `KBB99170|BB02030` aparece duas
vezes com a mesma quantidade. Somar daria 2 unidades onde a fonte diz 1 — e, num
kit de 1 SKU, isso muda a faixa de desconto de 0% para 5%. Travado em
`test_duplicata_identica_colapsa_e_nao_soma` e
`test_duplicata_colapsada_nao_muda_faixa_de_desconto`.

**Estado medido da BOM em 2026-09-25:** 1.516 kits ativos, 4.628 relações, 952
kits com vigência aberta hoje, **zero ciclos**, zero autorreferência, zero BOM
de dois níveis, `qty_per_kit` sempre em {1,2,3,4}, **uma** duplicata.

## 3. Ponte oferta → kit Protheus

Construída em `KitBridgeIndex` a partir de `gold.map_produto_codigo_gobeauty`,
`gold.dim_produto_gobeauty` e `silver.gobeaute_produto_cadastro`.
**Nunca a partir do CSV analítico do KITS-MAP-1** — aquele arquivo carrega
`CANDIDATE_REVIEW`, que nasce de composição empírica reconstruída de nota
fiscal, e usá-lo como configuração promoveria evidência a fato.

Métodos, todos por **igualdade exata**:

| método | origem |
|---|---|
| `seller_sku_is_protheus_kit` | o próprio SKU do canal é chave de kit na BOM |
| `brand_code_exact` | `(marca, código)` no `map_produto` → nó com `codigo_protheus` |
| `alias_code_exact` | `codigo_bling` / `codigo_tiny` / `codigo_shopify` / `codigo_omie` / `sku_antigo` |

Candidatos de **todos** os métodos são unidos antes de decidir: dois métodos
apontando para kits diferentes é ambiguidade, não precedência.

### Promoção automática

Permitida **somente** para `EXACT_DIRECT` e `EXACT_ALIAS`, e apenas com a marca
**provada**: as duas marcas — a da oferta e a do kit — conhecidas e iguais.

Bloqueados: `CANDIDATE_REVIEW`, `AMBIGUOUS`, `CROSS_BRAND_CONFLICT`, `UNMAPPED`.
A allowlist tem exatamente dois valores, então **um status novo nasce
bloqueado**, não liberado (`test_status_desconhecido_e_bloqueado_por_omissao`).

### Marca desconhecida bloqueia

Marca do kit ausente **não** é "sem conflito": é ausência de prova, e ausência
de prova bloqueia. Isso foi um defeito real encontrado durante a implementação:
`40126`–`40129` são chaves de kit de `apice_sheet`, em espaço de código do
Bling, anunciadas em loja Kokeshi. Com a checagem original (`oferta is not None
and kit is not None and oferta != kit`) elas passavam por `EXACT_DIRECT` e só
não viravam preço porque os componentes faltavam no catálogo — ou seja, estavam
bloqueadas **por acidente**. Hoje caem em `CANDIDATE_REVIEW` por regra.

Os sete Kokeshi `40125`–`40131` seguem todos bloqueados: `40125`, `40130` e
`40131` como `AMBIGUOUS` (o código casa consigo mesmo **e** com um kit `KAP990xx`
da Ápice), `40126`–`40129` como `CANDIDATE_REVIEW`. Travado em
`test_os_sete_kokeshi_40125_40131_seguem_bloqueados`.

### O que nunca entra

Similaridade de título, distância textual, preço, EAN *placeholder* e
composição empírica de NF. O resolvedor não tem código capaz de produzi-las.

## 4. Referência B2B do componente

Precedência explícita, sobre o **mesmo** `ReferenceIndex` que o matcher de
ofertas já usa:

1. **EAN de consumidor + marca**, exato e único;
2. **fallback `(marca, source_sku)`**, exato e único.

Recusa — e a recusa **não** cai para a chave seguinte:

| situação | motivo emitido |
|---|---|
| componente ausente do catálogo interno | `kit_component_not_in_catalog` |
| marca do componente ausente | `kit_component_brand_missing` |
| `dim_produto.marca_conflitante` ligada (21 dos 4.705 produtos) | `kit_component_brand_conflict` |
| mais de um candidato em qualquer das duas chaves | `kit_component_reference_ambiguous` |
| EAN e `(marca, sku)` resolvendo para linhas **diferentes** | `kit_component_reference_incompatible` |
| nenhuma das duas chaves resolve | `kit_component_reference_missing` |

### Por que o fallback existe

A planilha B2B carrega, em vários produtos, o **EAN da geração antiga de
código**. `KS03042` é "Creme Gel Facial Pele Plena": o `dim_produto` guarda
`7908790700137`, a referência guarda `7899459312597`. A chave por EAN não casa;
`(kokeshi, KS03042)` casa, e casa de forma única.

**Medido:** com índice só de EAN, **nenhum** dos seis kits Kokeshi tem preço.
Com o fallback, os seis têm. Quatro dos nove componentes envolvidos dependem
dele. Travado em `test_sem_fallback_nenhum_dos_seis_existe`.

O fallback não é flexibilização: é uma segunda chave **exata**, sujeita às
mesmas recusas da primeira.

## 5. Cálculo

```
referencia_componentes = SUM(qty_per_kit × referencia_componente)
unidades               = SUM(qty_per_kit)
referencia_do_kit      = referencia_componentes × (1 − desconto(unidades))
```

Faixas: 2 unidades −5%, 3 −10%, 4 ou mais −15%. Abaixo de 2, nenhum desconto.

**Unidades, nunca SKUs distintos.** 85 dos 1.516 kits ativos têm número de SKUs
diferente do número de unidades e **82 deles mudam de faixa** conforme a leitura.
Travado em `test_faixa_olha_unidades_nao_skus`.

Arredondamento: `ROUND_HALF_UP` para centavos, em `Decimal`. Isso importa em
pelo menos um caso real — `KKS00006` calcula 167,50 × 0,85 = 142,375 → **142,38**.

**Se qualquer componente não resolver, a referência do kit é `NULL` com o
motivo do primeiro componente irresolvido.** Nunca zero, nunca uma soma parcial.
As unidades continuam medidas: o que falta é o preço, não a composição.

## 6. Vocabulário de motivos

Todo `NULL` publicado carrega exatamente um valor de `KIT_REFERENCE_REASONS`:

`kit_bridge_not_promotable`, `kit_bridge_brand_mismatch`, `kit_bom_absent`,
`kit_component_not_in_catalog`, `kit_component_brand_missing`,
`kit_component_brand_conflict`, `kit_component_reference_missing`,
`kit_component_reference_ambiguous`, `kit_component_reference_incompatible`.

Travado em `test_todo_none_carrega_motivo_do_vocabulario`.

## 7. Limites conhecidos

- **Não está ligado.** Nenhuma flag, nenhum consumidor. `pma_domain` continua
  mandando todo kit para `kit_composition_missing`.
- **O teto não é a ponte, é a referência B2B.** Só 57 dos 612 componentes
  ativos da BOM têm linha na tabela B2B; 146 dos 1.516 kits têm *todos* os
  componentes referenciados. Mesmo com a ponte perfeita, o alcance para nesse
  número.
- **Marca do componente vem do `dim_produto`.** Onde o cadastro discorda — o
  caso conhecido é `KS03046`, `By Samia` no cadastro e `kokeshi` no
  `dim_produto`, cuja `marca_origem` é `prefixo_protheus` — o contrato segue a
  dimensão e só bloqueia quando ela própria declara `marca_conflitante`. A
  divergência está registrada na proposta de cadastro, não resolvida.
- **`kit_available_units` não é calculável** e por isso não está implementado
  aqui: ver seção 8 da reconciliação.
