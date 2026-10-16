# DRE-CMV-CMP-1 — CMV provisório por CMP (outubro/2026)

Status: implementado e medido em dry-run. **Nenhuma célula de outubro ganha margem estimada com as regras
aprovadas.** A migration 027 e o publicador estão no PR, mas não foram aplicados nem executados. Nada foi
escrito no Neon e não houve deploy. Este documento traz só percentuais e contagens; valores, SKUs e listas
ficam no relatório privado (`.local/dre_cmv_cmp/`).

## 1. O que é

`CMV provisório = unidades vendidas × CMP efetivo na competência`, por célula `competência × canal × marca`,
somado **só** sobre os SKUs com casamento válido. SKU sem casamento fica sem custo; nunca recebe CMP zero.

Ele serve para destravar o **cenário estimado** da DRE (DRE-PREMISSAS) enquanto o CMV fiscal da competência
não existe. **Nunca substitui o CMV fiscal**:

- tabela própria, `marts.fact_dre_cmv_provisorio_mensal` (migration 027), separada da 025;
- a API usa o fiscal sempre que ele estiver utilizável. O provisório só entra quando o fiscal está
  inutilizável **e** a cobertura atinge o mínimo;
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

**Reconciliação de receita** (fonte por SKU ÷ receita da célula, outubro até o corte):

| canal | faixa por marca |
|---|---|
| Mercado Livre | 0,992 – 0,995 |
| TikTok | 1,006 – 1,021 |
| Shopee | 0,978 – 1,014 (4 marcas) |

A origem da tabela diária da Shopee na API é uma [INFERÊNCIA] com evidência: os pedidos crus da API existem
para as mesmas 4 marcas, sem Kokeshi, e a tabela diária fica ~10% abaixo deles, o que é compatível com a
exclusão de cancelados.

## 3. Regras de CMP (as decididas, sem exceção)

Usa o índice de CMP e o resolvedor do DRE-CMP-SKU-1/2: filial SP/0301, MAGA SP, vigência entidade ×
competência (carry-forward só para frente, nunca retroativo), escopo marca → entidade, conflito sem escolha,
código exato provisório de baixa confiança, via GTIN restrita, KS03041/KS03042 sem CMP e kit sem regra.

Confiança por nível de casamento: GTIN = alta, volume = média, código exato provisório = baixa. A confiança da
célula é a do nível com mais receita coberta; em empate, vale a menor.

## 4. Cobertura de outubro (receita da fonte com CMP casado)

| canal | apice | barbours | kokeshi | lescent | rituaria |
|---|---:|---:|---:|---:|---:|
| TikTok | 23,6% | 29,8% | 13,1% | 6,9% | 0,0% |
| Mercado Livre | — | 6,6% | 10,6% | 2,3% | 0,0% |
| Shopee | 18,5% | 30,4% | ausente na fonte | 4,9% | 1,6% |

`—` = célula inexistente na DRE (Apice não vende no ML). Nenhum conflito entre candidatos válidos apareceu em
outubro, porque a venda chega com um único código.

**Por que a cobertura é baixa** (receita sem CMP, por canal):

| canal | kit (código ou nome) | código fora das planilhas, sem GTIN | outros (KS a confirmar, evidência divergente) |
|---|---:|---:|---:|
| TikTok | 75% | 23% | 2% |
| Mercado Livre | 75% | 18% | 7% |
| Shopee | 63% | 37% | 0% |

1. **Kits de marketplace** são a maior parte da receita, e a regra de kit não está aprovada.
2. O `seller_sku` do marketplace é outro sistema de códigos que o código próprio do ERP. As fontes de ML e
   TikTok não trazem EAN, então a via GTIN quase não tem como agir. Um de-para pelo cadastro
   (código ↔ código antigo, escopado por marca) foi **simulado e não aplicado**: acrescentaria menos de 1 ponto,
   porque esses códigos não estão no cadastro.

## 5. Efeito no cenário estimado

Piso de cobertura para o provisório entrar na margem: **90%** [DECISÃO PENDENTE]. Abaixo dele, a receita sem
custo inflaria a margem.

Simulação local de outubro (Neon só em leitura, provisório em memória, nada publicado):

| estado do cenário estimado | células |
|---|---:|
| `calculavel` | 0 |
| `parcial` (provisório usado) | 0 |
| `nao_calculavel` | 14 |

Motivo explícito em cada célula: "cmv provisório por CMP insuficiente: cobre X% (mínimo 90%)", ou "vendas
por SKU ausentes na fonte de API" na Shopee Kokeshi.

## 6. Armazenamento — migration 027 (não aplicada)

`marts.fact_dre_cmv_provisorio_mensal`, PK `competencia × canal × marca`. Só agregados da célula, sem SKU.

| CHECK | garante |
|---|---|
| `ausente_na_fonte` ⇔ receita da fonte NULL, com CMV e cobertura NULL | ausência explícita, nunca zero |
| CMV NULL ⇔ nenhum SKU coberto | sem casamento não vira CMV zero |
| cobertura em 0..1 e `<> 'NaN'`; não negativos | faixa |
| receita coberta ≤ receita da fonte; cobertos + sem CMP = total | consistência |
| allowlist de canal e marca; `regra_temporal = 'entidade_vigencia'` | a regra decidida é a única gravável |
| `cmp_fontes_sha256` | procedência das planilhas de CMP |

## 7. Publicador — `python -m pipelines.dre.cmv_cmp_provisorio`

```bash
python -m pipelines.dre.cmv_cmp_provisorio --pasta dre --competencia 2026-10            # dry-run
python -m pipelines.dre.cmv_cmp_provisorio --pasta dre --competencia 2026-10 --apply    # NÃO executado
```

- dry-run por padrão: lê Neon e Data Mart em `READ ONLY` e grava a matriz por SKU e as células em `--saida`
  (privado), e imprime só percentuais;
- `--apply`: transação única, `pg_try_advisory_xact_lock(918140027)`, DELETE só da competência, contagem de
  outras competências conferida, auditoria em `audit.source_sync_run` e nenhum retry.

## 8. Testes

- `pipelines/tests/test_dre_cmv_cmp_provisorio.py`: CMV = unidades × CMP, carry-forward, vigência sem
  retroação, ausência ≠ zero, cobertura parcial, conflito, via GTIN, Kokeshi ausente na fonte,
  SKU ambíguo/sem SKU/kit, KS sem CMP, resumo público sem valor e dry-run sem escrita.
- `pipelines/tests/test_dre_cmv_cmp_provisorio_integracao.py` (PostgreSQL descartável): CHECKs da 027,
  ausente gravado como NULL, idempotência, escopo por competência, rollback, lock e migration ausente.
- `apps/api/tests/test_dre_cmv_provisorio.py`:
  - provisório entra só sem fiscal e acima do piso;
  - abaixo do piso ou ausente na fonte → `nao_calculavel` com motivo;
  - **contraprova: com fiscal utilizável, o provisório nunca o substitui**;
  - cobertura agregada pela soma;
  - rota com a 027 ausente;
  - lote não concluído ignorado.

## 9. Decisões externas para destravar

1. **Regra de kit** (CMP do kit × soma dos componentes): é o maior bloqueio, com 63% a 75% da receita sem CMP.
2. **De-para `seller_sku` → código próprio do ERP** por marca, aprovado e versionado. O cadastro atual não o
   cobre.
3. **Piso de cobertura** para usar o provisório (proposta: 90%).
4. Confirmação de KS03041/KS03042 e do de-para temporal dos conflitos (pendências do DRE-CMP-SKU-2).
5. Origem da tabela diária da Shopee (confirmar que é 100% API).

## 10. Próximo gate de publicação

Só depois que os itens 1–3 elevarem a cobertura acima do piso em alguma célula:

1. merge do PR;
2. `alembic upgrade 027`;
3. publicador em dry-run e conferência;
4. `--apply`;
5. deploy da API.

Sem isso, publicar outubro hoje só trocaria o motivo das 14 células de "CMV provisório não publicado" para
"cobertura insuficiente". O estado continuaria `nao_calculavel`.
