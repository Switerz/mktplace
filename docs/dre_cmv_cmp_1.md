# DRE-CMV-CMP-1/2 — CMV provisório por CMP e kits (outubro/2026)

Status: implementado e medido em dry-run. Com as regras de kit, a cobertura de outubro sobe de 14,7% (sem kit)
para 15,5% (+ homogêneo) e **47,3%** (+ misto) da receita, mas **nenhuma célula chega a 100%**. Por isso nenhuma ganha margem numérica no
cenário estimado.

A migration 027 e o publicador estão no PR, mas não foram aplicados nem executados. Nada foi escrito no Neon e
não houve deploy. Este documento traz só percentuais e contagens; valores, SKUs, listas de kits e o de-para ficam
no relatório privado (`.local/dre_cmv_cmp/`).

## 1. O que é

`CMV provisório = unidades vendidas × CMP efetivo na competência`, por célula `competência × canal × marca`,
somado **só** sobre os SKUs com casamento válido. SKU sem casamento fica sem custo; nunca recebe CMP zero.

Ele serve para destravar o **cenário estimado** da DRE (DRE-PREMISSAS) enquanto o CMV fiscal da competência
não existe. **Nunca substitui o CMV fiscal**:

- tabela própria, `marts.fact_dre_cmv_provisorio_mensal` (migration 027), separada da 025;
- a API usa o fiscal sempre que ele estiver utilizável. O provisório só entra quando o fiscal está
  inutilizável **e** a cobertura é de **100%**;
- mesmo assim, o cenário nunca passa de `parcial`, e a linha de CMV vem rotulada como `cmp_provisorio`;
- o provisório também aparece como linha informativa de comparação (`cmv_provisorio_cmp`, nunca somada);
- a DRE conhecida/realizada não muda.

## 2. Fontes de vendas — só API, só leitura

| canal | fonte | grão | chave para o CMP |
|---|---|---|---|
| Mercado Livre | `gold.ml_produto_diario` (pedidos da API) | dia × anúncio | `seller_sku` |
| TikTok | `api.tiktok_analytics_skus` + `gold.tiktok_inventory_daily` | dia × `sku_id` | `seller_sku` do inventário; `sku_id` com mais de um código no histórico fica `sku_ambiguo` |
| Shopee | `gold.shopee_product_daily` (pedidos da API) | dia × SKU | `sku` e EAN |

- Cada fonte é recortada até a **mesma data máxima** que a DRE usa na receita da célula.
- Shopee usa só as 4 marcas servidas pela API. A Kokeshi (export manual) fica `ausente_na_fonte`, com valores
  NULL e nunca zero.
- Reconciliação de receita (fonte por SKU ÷ receita da célula): ML 0,992–0,995; TikTok 1,006–1,021;
  Shopee 0,978–1,014.
- A origem da tabela diária da Shopee na API é uma [INFERÊNCIA] com evidência: os pedidos crus da API existem
  para as mesmas 4 marcas, sem Kokeshi.

## 3. Regras de CMP (as decididas, sem exceção)

Usa o índice de CMP e o resolvedor do DRE-CMP-SKU-1/2: filial SP/0301, MAGA SP, vigência entidade ×
competência (carry-forward só para frente, nunca retroativo), escopo marca → entidade, conflito sem escolha,
código exato provisório de baixa confiança, via GTIN restrita e KS03041/KS03042 sem CMP.

Confiança: GTIN = alta, volume = média, código exato provisório = baixa. Um kit herda a **menor** confiança
entre os seus componentes. A confiança da célula é a da faixa com mais receita coberta; em empate, vale a menor.

## 4. Kits — fontes de composição e regra (DRE-CMV-CMP-2)

### 4.1 Fontes inventariadas

| fonte | natureza | casa com os kits vendidos em outubro (% da receita de kit) | uso |
|---|---|---:|---|
| `raw.bling_kit_estrutura` | **BOM explícita do ERP Bling**, por conta = marca, carga única de 30/09/2026 | 99,8% | **usada** |
| `raw.protheus_kit_components` | BOM do Protheus, com vigência | 3,8% | não usada (outro sistema de códigos) |
| `gold.bridge_kit_componente_gobeauty` | ponte derivada | 3,8% | não usada |
| `raw.apice_kit_components_hist` | histórico Apice | 3,8% | não usada |
| `silver.kits_composicao_marketplace` | composição **inferida** por co-ocorrência em NF | 99,9% | **proibida** (inferência, não BOM) |
| `silver.shopify_kit_components` | casamento por **semelhança textual** | — | **proibida** |
| `gold.kits_marketplace_nf_daily` | detecção de kit em NF, sem composição | 100% | só como evidência |
| `marts.fact_kit_reference_daily` (Neon) | ponte de kits do PMA | — | vazia |
| cadastro de produtos (`tipo = KT`) | identifica kit, sem composição | 3,8% | só classificação |
| pasta `dre/` | planilhas de CMP | — | sem composição de kit |

Qualidade da BOM do Bling: sem linhas duplicadas, sem o mesmo componente com quantidades diferentes,
quantidades inteiras de 1 a 6, nenhum componente sem código e nenhum kit aninhado. 18 códigos de kit existem
em mais de uma conta, por isso o escopo é sempre `conta = marca`.

### 4.2 Regra

- **kit homogêneo** (1 componente): CMP do kit = quantidade × CMP do componente;
- **kit misto** (2+ componentes): soma de quantidade × CMP de cada componente;
- **todos** os componentes precisam de CMP válido (mesmas regras de casamento). Se faltar um, o kit fica sem
  custo: nunca custo parcial;
- se o próprio código do kit também casa com um CMP direto e os dois divergem → `conflito_kit`, sem escolha;
- a BOM nunca contorna um conflito do código direto nem o bloqueio de KS03041/KS03042;
- composição ambígua (componente sem código, quantidade inválida ou o mesmo componente com quantidades
  diferentes) → `kit_composicao_ambigua`, sem custo.

### 4.3 Classificação dos SKUs vendidos em outubro que estão na BOM

| classe | SKUs | % da receita total | resultado |
|---|---:|---:|---|
| homogêneo, todos os componentes com CMP | 17 | 0,8% | coberto |
| homogêneo, componente sem CMP | 196 | 11,9% | sem custo |
| misto, todos os componentes com CMP | 264 | 31,9% | coberto |
| misto, componente sem CMP | 353 | 30,8% | sem custo |
| composição ambígua | 0 | 0,0% | — |
| kit sem composição em nenhuma fonte explícita | 23 | 0,1% | sem custo |

## 5. Cobertura de outubro (receita da fonte com CMP casado)

| célula | sem regra de kit | + homogêneo | + misto |
|---|---:|---:|---:|
| TikTok · apice | 23,6% | 23,6% | 37,9% |
| TikTok · barbours | 29,8% | 33,1% | 64,5% |
| TikTok · kokeshi | 13,1% | 13,2% | 63,2% |
| TikTok · lescent | 6,9% | 6,9% | 7,2% |
| TikTok · rituaria | 0,0% | 0,1% | 0,1% |
| ML · barbours | 6,6% | 9,0% | 80,0% |
| ML · kokeshi | 10,6% | 10,8% | 60,1% |
| ML · lescent | 2,3% | 2,3% | 3,1% |
| ML · rituaria | 0,0% | 0,1% | 0,1% |
| Shopee · apice | 18,5% | 18,5% | 35,6% |
| Shopee · barbours | 30,4% | 31,4% | 77,4% |
| Shopee · kokeshi | ausente na fonte | — | — |
| Shopee · lescent | 4,9% | 4,9% | 5,5% |
| Shopee · rituaria | 1,6% | 1,6% | 1,6% |

Por canal, com as duas regras: ML 50,5%, TikTok 46,7%, Shopee 42,2%. Da receita coberta, 37% tem confiança alta,
8% média e 55% baixa.

**O que ainda falta** é, sobretudo, **componente de kit sem CMP**:
- Rituaria (100% do que falta): os componentes da BOM usam outro sistema de códigos que a planilha da MAGA;
- Lescent (77%): os componentes usam o código numérico do Bling, enquanto o CMP do CMV-LCV usa outro;
- Barbours (73%): componentes novos sem série de CMP em SP;
- Apice (74%): componentes ausentes ou com GTIN repetido em códigos de CMP diferente;
- Kokeshi (81%): a maior parte depende de KS03041/KS03042, ainda bloqueados.

**De-para mínimo a pedir** (lista nominal no relatório privado): cerca de **46 códigos de componente** cobrem
~90% da receita de kit ainda sem custo — Apice 9, Barbours 6, Kokeshi 4 (2 deles são os KS a confirmar),
Lescent 18 e Rituaria 9.

Simulação **não aplicada**: usar os pares código fiscal ↔ código canônico que o próprio Data Mart já carrega
(únicos por marca) como de-para levaria a Lescent a 90–95% (TikTok e Shopee) e a Rituaria a 10–19%. Isso pode
servir de base para o de-para, mas precisa ser aprovado e versionado antes de entrar.

## 6. Margem bruta após CMP por produto

Para cada SKU coberto (548 em outubro), o relatório privado traz:
- receita e unidades;
- CMP unitário e CMV;
- **margem bruta após CMP** (`receita − CMV`) e o percentual;
- método de casamento, confiança e a cobertura da célula.

Na célula:
- a **margem numérica só existe com 100% de cobertura**. Hoje nenhuma célula chega lá;
- abaixo disso, a única margem calculada é a da **parcela coberta**, rotulada como tal
  (`margem_bruta_parcela_coberta`), sem atribuir custo à parcela descoberta;
- não existe regra explícita e auditável de estimativa para a parcela não coberta. Sem ela, nada é extrapolado.

O nome continua "margem bruta após CMP": não é margem de contribuição nem margem líquida.

## 7. Efeito no cenário estimado

O provisório só entra na margem do cenário com **100% de cobertura**. Antes, a proposta era um piso de 90%; foi
removida porque deixaria a parcela descoberta com custo zero. Até 99,99% de cobertura, o cenário fica
`nao_calculavel`, com o motivo explícito.

Simulação local de outubro (Neon só em leitura, provisório em memória, nada publicado): **14 de 14 células
`nao_calculavel`**. A maior cobertura é a do ML · barbours, com 80,0%.

## 8. Armazenamento — migration 027 (não aplicada)

`marts.fact_dre_cmv_provisorio_mensal`, PK `competencia × canal × marca`. Só agregados da célula, sem SKU,
incluindo a receita coberta por regra de kit (`receita_kit_homogeneo`, `receita_kit_misto`).

| CHECK | garante |
|---|---|
| `ausente_na_fonte` ⇔ receita da fonte NULL, com CMV e cobertura NULL | ausência explícita, nunca zero |
| CMV NULL ⇔ nenhum SKU coberto | sem casamento não vira CMV zero |
| cobertura em 0..1 e `<> 'NaN'`; não negativos | faixa |
| receita coberta ≤ receita da fonte; cobertos + sem CMP = total | consistência |
| allowlist de canal e marca; `regra_temporal = 'entidade_vigencia'` | a regra decidida é a única gravável |
| `cmp_fontes_sha256` | procedência das planilhas de CMP |

## 9. Publicador — `python -m pipelines.dre.cmv_cmp_provisorio`

```bash
python -m pipelines.dre.cmv_cmp_provisorio --pasta dre --competencia 2026-10            # dry-run
python -m pipelines.dre.cmv_cmp_provisorio --pasta dre --competencia 2026-10 --apply    # NÃO executado
```

- dry-run por padrão: lê Neon e Data Mart em `READ ONLY`. Em `--saida` (privado) grava a matriz por SKU, a
  margem por produto, as células e a cobertura por regra de kit. No stdout imprime só percentuais;
- `--apply`: transação única, `pg_try_advisory_xact_lock(918140027)`, DELETE só da competência, contagem de
  outras competências conferida, auditoria e nenhum retry.

## 10. Testes

- `pipelines/tests/test_dre_cmv_cmp_provisorio.py`:
  - CMV = unidades × CMP, vigência sem retroação, ausência ≠ zero, cobertura parcial, conflito, GTIN, Kokeshi
    ausente, KS e resumo público;
  - **kits:** homogêneo, misto com a menor confiança, componente sem CMP sem custo parcial, escopo por marca,
    composição ambígua, regras ligadas separadamente, conflito direto × BOM, BOM não contorna KS nem conflito;
  - **margem:** margem por SKU, margem da célula só com 100% e margem da parcela coberta rotulada.
- `pipelines/tests/test_dre_cmv_cmp_provisorio_integracao.py` (PostgreSQL descartável): CHECKs da 027, ausente
  gravado como NULL, idempotência, escopo por competência, rollback, lock e migration ausente.
- `apps/api/tests/test_dre_cmv_provisorio.py`: provisório só com 100% e sem fiscal; 99,99% não entra; ausente
  na fonte; **contraprova de que o provisório não substitui o fiscal**; cobertura agregada pela soma; rota com a
  027 ausente; lote não concluído ignorado.

## 11. Decisões externas para destravar

1. **De-para de componentes de kit → código com CMP** (cerca de 46 códigos para ~90%; a lista está no relatório
   privado). Alternativa: aprovar os pares fiscais do Data Mart como de-para versionado.
2. **Regra explícita de estimativa** para a parcela não coberta, se a margem da célula tiver de existir abaixo
   de 100%. Hoje ela não existe, e a margem fica ausente.
3. Confirmação de KS03041/KS03042 e do de-para temporal dos conflitos (pendências do DRE-CMP-SKU-2).
4. Origem da tabela diária da Shopee (confirmar que é 100% API).

## 12. Próximo gate de publicação

Só depois que o item 1 (ou o 2) levar alguma célula a 100%:

1. merge do PR;
2. `alembic upgrade 027`;
3. publicador em dry-run e conferência;
4. `--apply`;
5. deploy da API.

Publicar outubro hoje só trocaria o motivo das 14 células de "CMV provisório não publicado" para
"cobertura insuficiente". O estado continuaria `nao_calculavel`.
