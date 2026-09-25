# Reconciliação antes × depois — referência de kits (KITS-MAP-2)

Gerado em **2026-09-25** por
[`apps/api/tools/reconcile_kits_map_2.py`](../../apps/api/tools/reconcile_kits_map_2.py),
somente leitura, contra as fontes reais.

```
python tools/reconcile_kits_map_2.py \
    --out ../../docs/reconciliation/kits_map_2_reconciliacao_2026-09-25.csv \
    --diagnostico ../../docs/reconciliation/kit_map_1_candidatos_2026-09-25.csv \
    --json
```

Artefatos:

- [`kits_map_2_reconciliacao_2026-09-25.csv`](kits_map_2_reconciliacao_2026-09-25.csv) — 927 linhas, uma por oferta com sinal de kit;
- [`kits_map_2_cadastro_proposta_2026-09-25.csv`](kits_map_2_cadastro_proposta_2026-09-25.csv) — 504 linhas, todas `PENDENTE`;
- contrato: [`docs/contrato_bom_kits.md`](../contrato_bom_kits.md).

**Nada foi publicado.** Nenhuma flag, nenhum publisher, nenhuma migration,
nenhuma escrita. O "depois" é o que o contrato *permitiria* publicar.

---

## 1. Antes × depois

**Antes:** nenhum kit tem referência. Todo `kit_confirmed`/`kit_suspected` sai
do elegível com `kit_composition_missing`. São 927 ofertas, 0 com preço.

**Depois:** **6 ofertas** passam a ter referência — todas no TikTok, todas
ativas e `in_scope`.

| seller_sku | kit Protheus | comp. | unidades | base | faixa | **referência** |
|---|---|---:|---:|---:|---:|---:|
| 40006 | KKS00006 | 5 | 5 | 167,50 | −15% | **R$ 142,38** |
| 40009 | KKS00032 | 3 | 3 | 103,70 | −10% | **R$ 93,33** |
| 40018 | KKS00017 | 2 | 2 | 92,80 | −5% | **R$ 88,16** |
| 40012 | KKS00010 | 2 | 2 | 79,80 | −5% | **R$ 75,81** |
| 40010 | KKS00008 | 2 | 2 | 68,80 | −5% | **R$ 65,36** |
| 40019 | KKS00015 | 2 | 2 | 55,80 | −5% | **R$ 53,01** |

**Os seis valores do gate reconferem exatamente**, sem ajuste. A fotografia é a
mesma de 25/09 e o snapshot de referência continua o de 247 linhas.

Dos 15 casamentos de componente que sustentam esses seis kits, **8 vieram do
fallback `(marca, source_sku)`** e 7 do EAN. Com um índice só de EAN, nenhum dos
seis existe.

## 2. Status da ponte

| status | ofertas | promove? |
|---|---:|---|
| `EXACT_DIRECT` | 10 | sim |
| `CANDIDATE_REVIEW` | 7 | **não** |
| `AMBIGUOUS` | 6 | **não** |
| `UNMAPPED` | 904 | **não** |

**Candidatos que permaneceram bloqueados: 13** (7 `CANDIDATE_REVIEW` + 6
`AMBIGUOUS`).

## 3. Motivos de `NULL` — 921 ofertas

| motivo | ofertas |
|---|---:|
| `kit_bridge_not_promotable` | 917 |
| `kit_component_reference_missing` | 4 |

As 4 são `EXACT_DIRECT` com ponte válida e composição completa, barradas só pela
tabela B2B: `40015`→KKS00027, `40002`→KKS00002, `40004`→KKS00004,
`40025`→KKS00005. Faltam **exatamente dois componentes** na referência:

- `KS02006` — Tônico Facial Pele de Porcelana Kokeshi (EAN `7908790700052`);
- `KS03022` — Sérum Facial Antimanchas Pele de Porcelana (EAN `7908790700014`).

Nenhum dos dois está na planilha nem por SKU nem por EAN. **Duas linhas de
cadastro B2B destravariam 4 ofertas**, levando a cobertura segura de 6 para 10.

## 4. Segunda opinião contra o KITS-MAP-1

O runtime reconstrói a ponte do banco; o CSV do gate 1 é lido só para conferir.

| diagnóstico → runtime | ofertas | leitura |
|---|---:|---|
| `EXACT_ALIAS` → `EXACT_DIRECT` | 10 | mesmas ofertas, método diferente: o runtime alcança pelo `map_produto`, o diagnóstico tinha alcançado pelo alias |
| `CROSS_BRAND_CONFLICT` → `CANDIDATE_REVIEW` | 7 | os Kokeshi `40125`–`40131`. Bloqueados nos dois, rótulo diferente |
| `CANDIDATE_REVIEW` → `UNMAPPED` | 571 | a composição empírica de NF **desaparece** do runtime, como deve |
| `AMBIGUOUS` → `AMBIGUOUS` | 6 | — |
| `UNMAPPED` → `UNMAPPED` | 333 | — |

**Invariante de segurança verificada: nenhuma oferta promovida no runtime está
bloqueada no diagnóstico** (`promovidas_no_runtime_e_bloqueadas_no_diagnostico: []`).

### Um fail-open encontrado e fechado durante a implementação

A primeira versão devolvia `EXACT_DIRECT` para 17 ofertas, incluindo
`40126`–`40129`. Esses códigos são chaves de kit de `source = 'apice_sheet'`,
no espaço de código do Bling, anunciados em loja Kokeshi — e a checagem de marca
original deixava passar porque a marca do kit era **desconhecida**, não
divergente. Eles não viravam preço apenas porque os componentes faltavam no
catálogo: estavam bloqueados por acidente. Hoje `is_promotable` exige as duas
marcas conhecidas e iguais, e eles caem em `CANDIDATE_REVIEW` por regra.

## 5. Impacto do fallback fora dos kits

Medição apenas — o serving não foi alterado.

| medida | ofertas |
|---|---:|
| ofertas não-kit já cobertas pelo matcher atual | 267 |
| recuperáveis pelo caminho interno **por EAN** | 46 |
| recuperáveis **só** pelo fallback `(marca, source_sku)` | **18** |

Hoje o terceiro método do matcher (`MATCH_INTERNAL`) vai `produto_sk → EAN →
referência`. Estender esse caminho para também tentar `(marca, codigo_protheus)`
recuperaria 18 ofertas comuns, pela mesma causa dos kits: a planilha B2B carrega
o EAN da geração antiga. **Mudança de serving, portanto fora deste gate.**

## 6. Contrato da BOM em execução real

- 4.691 linhas lidas, **carga aceita** — nenhuma violação de contrato;
- 1.516 kits indexados;
- duplicata colapsada: `KBB99170|BB02030`, uma só, como esperado;
- zero ciclo, zero autorreferência, zero quantidade não positiva.

## 7. Proposta de cadastro (`PENDENTE`, não consumida por runtime)

[`kits_map_2_cadastro_proposta_2026-09-25.csv`](kits_map_2_cadastro_proposta_2026-09-25.csv),
504 linhas, **100% `PENDENTE`**. Colunas `responsavel`, `estado`
(`PENDENTE`/`APROVADO`/`REJEITADO`) e `decidido_em` estão vazias, à espera de
gente. Nenhum código lê este arquivo.

### 7.1 Chaves de canal — 501 linhas / 387 códigos distintos

O gate falava em **397 chaves**: é o total de `seller_sku` distintos com sinal
de kit. A fila exclui os **10** que já têm ponte exata, restando **387**. As
linhas são 501 porque o cadastro grava por `(marca, código)` e ~114 códigos são
anunciados por mais de uma marca — consequência direta de o código Bling ser
**por conta**, não global.

**289 linhas / 208 códigos** trazem composição empírica para pré-preenchimento.
O gate falava em 224: são 208 empíricos **+ 16** que já vinham da BOM
autoritativa do Protheus. A proposta lista só os 208, porque os outros 16 não
precisam de cadastro novo.

Cada linha diz quantos componentes, quantas unidades e com quantos pedidos de
evidência — para o revisor saber o que está aceitando. **A composição não é
pré-aprovada.**

### 7.2 De-para de códigos Protheus — 3 candidatos, com ressalva

**O de-para não é derivável das fontes atuais.** O único sinal estrutural
disponível é "mesmo código Bling alcançando dois códigos Protheus":

- **sem** restringir marca: 83 pares, quase todos lixo — o código Bling colide
  entre contas, e o resultado casa "COND BLND ANTFRZ 200ML" com
  "DEO COLONIA NO 8 - 25ML";
- **com** a marca: 3 pares, e **1 deles é comprovadamente falso**
  (`KBB99011`→`KBB99007`, "Caixa com 3 brindes" × "Caixa com 2 brindes", EANs
  `4000000000011` × `4000000000007`).

Por isso as linhas saem como **candidatas a conferência**, com a coluna `alerta`
marcando EAN divergente. Os pares de geração de código observados no KITS-MAP-1
(`KS03042`↔`KS03011`, `KS03043`↔`KS03009`, `KS03044`↔`KS03010`,
`KS03046`↔`KS02005`, `KS03020`↔`KS03021`, `KS02007`↔`KS02004`) **não** aparecem
aqui porque nenhum sinal estrutural os liga — foram identificados por inspeção.
Esse de-para é entrega humana.

### 7.3 Divergência de marca — RESOLVIDA no KITS-PMA-3

`KS03046` era **`By Samia`** em `silver.gobeaute_produto_cadastro` e
**`kokeshi`** em `gold.dim_produto_gobeauty`. **Resolvido:** as notas fiscais
arbitram — `gold.bling_all_brands_nfes_gproducts` traz **12.335 linhas, 100%
`kokeshi`**, de 01/07 a 24/09/2026, zero de "By Samia". O cadastro é a
inconsistência, não a autoridade, e entra na fila como
`inconsistencia_de_marca` com estado `PENDENTE`.

Ver [`kits_pma_3_serving_2026-09-25.md`](kits_pma_3_serving_2026-09-25.md) §4.
A contagem da proposta mudou com a seção nova: **563 linhas**, não 504.

## 8. Estoque Full — registro, sem classificação

Nenhum kit foi classificado nesta rodada. Registrado formalmente:

- **12 dos 22** kits do Estoque Full ganham composição (pela via empírica do
  KITS-MAP-1, que segue bloqueada para promoção);
- **zero** ganha `kit_available_units`;
- **todos os 22 permanecem `KIT_NAO_CONCILIADO`**, que é o estado atual medido
  em `marts.fact_shopee_fbs_stock_daily` de 2026-09-25;
- **`raw.webgex_posicao_estoque_*` não é fonte utilizável de saldo.** As 10.139
  linhas das três tabelas (`_gb`, `_ap`, `_az`) têm `status = 'PENDENTE'` em
  **100%** dos casos: é log de movimento pendente, não posição. 3.126 de 7.418
  produtos com saldo negativo, amplitude de −806.225 a +710.958.

### Fontes que ainda poderiam dar saldo vendável por componente

Investigação **somente leitura**, sem implementação:

| fonte | chave | cobertura dos 612 componentes ativos | frescor | ressalva |
|---|---|---:|---|---|
| `silver.bling_produtos` | código Bling | **322** com linha, **309** com saldo ≥ 0 | 2026-09-18 | melhor candidata; 298 das 4.352 linhas com saldo negativo |
| `raw.bling_estoque_saldos_*` (6 contas) | código Bling | 322 | por conta | mesma origem, grão por depósito |
| `gold.shopify_inventory_current` | Shopify | não medida | — | 759 de 3.504 linhas negativas; canal diferente do marketplace |
| `marts.fact_shopee_fbs_stock_daily` | `item_id`/`model_id` | — | 2026-09-25 | é saldo do **anúncio**, não do componente; não decompõe |
| `raw.webgex_posicao_estoque_*` | Protheus | — | — | **descartada**: 100% `PENDENTE` |

O gargalo de `silver.bling_produtos` é o mesmo de todo o resto: só **329 dos
612** componentes têm `codigo_bling` no `dim_produto`. A ponte cadastral é o
bloqueio, não a existência do dado.
