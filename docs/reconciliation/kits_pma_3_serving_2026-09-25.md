# Serving da referência de kit — antes × depois (KITS-PMA-3)

Medido em **2026-09-25** com
`python -m pipelines.kit_reference_publisher --json` (modo **diagnóstico**:
somente leitura, sem lock, sem transação de destino, sem escrever).

> **Nada foi publicado em produção.** A migration 022 **não foi aplicada**, o
> publisher **não rodou com `--apply`** e nenhuma flag foi criada. Enquanto
> `marts.fact_kit_reference_daily` não existir no Neon, o serving lê zero linha
> e a tela permanece idêntica. Estado: **`READY_FOR_KIT_SERVING_DEPLOY`**.

---

## 1. Correção do estado declarado no gate anterior

O relatório do KITS-MAP-2 disse "cobertura segura recuperada: 6 ofertas". Isso
descrevia o que o **contrato provava**, não o que o **produto servia**. Até
este gate:

- `pma_kit_bom.py` era consumido **apenas** pelas ferramentas de reconciliação;
- `monitoramento_preco_service.py` não o importava;
- nenhuma referência derivada estava materializada no Neon;
- a tela mostrava as seis ofertas como **sem referência**.

Continua verdade **hoje**, e só deixa de ser depois de aplicar a migration e
rodar o publisher.

## 2. O que o publisher produziria na fotografia atual

Fotografia `2026-09-25`, snapshot B2B `pma-ref:20260923T193400Z`.

| canal | ofertas | sinal de kit | linhas publicadas | `resolved` | `blocked` |
|---|---:|---:|---:|---:|---:|
| Shopee | 695 | 355 | **0** | 0 | 0 |
| TikTok | 1.221 | 572 | **10** | **6** | 4 |

**Nenhuma oferta Shopee muda** — as 355 são `UNMAPPED`, exatamente como
previsto.

### Status da ponte no TikTok

| status | ofertas | publica? |
|---|---:|---|
| `EXACT_DIRECT` | 10 | sim (6 com valor, 4 bloqueadas por componente) |
| `CANDIDATE_REVIEW` | 7 | **não** |
| `AMBIGUOUS` | 6 | **não** |
| `UNMAPPED` | 549 | **não** |

**13 candidatos permanecem bloqueados**, incluindo os sete Kokeshi
`40125`–`40131`. Nenhuma linha `PENDENTE`, candidata, ambígua ou de conflito
entra na tabela — e o CHECK `ck_fkrd_bridge_status` a recusaria mesmo que o
código tentasse.

### Os seis valores

Reproduzidos pelo publisher contra as fontes reais, sem ajuste:

`142.38` · `93.33` · `88.16` · `75.81` · `65.36` · `53.01`

### As quatro bloqueadas

`40015`→KKS00027, `40002`→KKS00002, `40004`→KKS00004, `40025`→KKS00005, todas
com `reason = kit_component_reference_missing`. Faltam **dois** componentes na
planilha B2B: `KS02006` (Tônico Facial Pele de Porcelana, EAN
`7908790700052`) e `KS03022` (Sérum Facial Antimanchas, EAN `7908790700014`).
Duas linhas de cadastro B2B levam a cobertura de 6 para 10.

Elas são publicadas com `status = 'blocked'` e valor `NULL`: a tabela responde
"por que este kit não tem preço" sem exigir rodar o diagnóstico de novo.

## 3. Efeito esperado na tela, depois do deploy

| métrica (TikTok) | antes | depois | Δ |
|---|---:|---:|---:|
| `kit_reference_derived` | — | 6 | +6 |
| `eligible_offers` | E | E + 6 | +6 |
| `comparable_offers` | C | C + 6 | +6 |
| `no_reference_breakdown[kit_composition_missing]` | K | K − 6 | −6 |
| `kit_confirmed` / `kit_suspected` | — | **inalterado** | 0 |
| Shopee, qualquer métrica | — | **inalterado** | 0 |
| Mercado Livre, qualquer métrica | — | **inalterado** | 0 |

As identidades continuam fechando, agora com o termo novo:

```
eligible_offers   = active_offers - kit_confirmed - kit_suspected
                    - fora_de_escopo_nao_kit + kit_reference_derived
comparable_offers + soma(non_comparable_reasons) = eligible_offers
soma(no_reference_breakdown) = kpis.no_reference_count
```

Travadas em `test_denominador_sobe_exatamente_pelos_kits_derivados`,
`test_particao_do_denominador_continua_fechando`,
`test_balde_de_kit_sem_referencia_encolhe` e
`test_breakdown_continua_somando_o_cartao`.

> Os números absolutos de `E`, `C` e `K` **não** são fixados aqui de propósito:
> a fotografia avança todo dia às 07:30. O que é contrato é o **delta** e as
> identidades. Se o delta vier diferente de +6 no dia do deploy, a causa é a
> fotografia ter mudado — o diagnóstico do publisher diz quantas linhas ele
> publicaria naquele dia, e é esse número que deve bater.

## 4. `KS03046` — risco fechado

Confirmado em `gold.bling_all_brands_nfes_gproducts`: **12.335 linhas de NF,
100% `marca = 'kokeshi'`**, um único `item_codigo`, de 01/07 a 24/09/2026,
**zero** linha de "By Samia".

O cadastro `By Samia` é **inconsistência cadastral**, e entra na fila de
aprovação como `inconsistencia_de_marca`, com `alvo_proposto = kokeshi` e
estado `PENDENTE`. O contrato não muda: ele já seguia `dim_produto.marca`.

Os sufixos de descrição ("190ML" × "200ml", "E6", "F5") **não** criam produtos
distintos; lê-se como lote ou variação de embalagem, e isso **permanece
inferência**, não fato. Nenhuma rotina deste gate interpreta sufixo.

## 5. Proposta de cadastro — recontagem

`kits_map_2_cadastro_proposta_2026-09-25.csv`, **563 linhas, 100% `PENDENTE`**:

| seção | linhas | observação |
|---|---:|---|
| `chave_de_canal` | 501 (387 códigos) | 289 com composição empírica para pré-preenchimento |
| `de_para_protheus` | 3 | 1 comprovadamente falso, marcado em `alerta` |
| `inconsistencia_de_marca` | **59** | só 1 arbitrada pela NF: `KS03046` |

A seção de marca é nova neste gate. Ela **exclui** diferenças de acento
(`ápice` × `apice`, `rituária` × `rituaria`) — que não pedem decisão — e só
declara "a NF arbitrou" com pelo menos 10 linhas de nota. `RT01009` tem uma
única linha, de abril, e por isso **não** é arbitrada.

## 6. Bloqueios, separados

| bloqueio | alcance | o que destrava |
|---|---:|---|
| **B2B** | 4 ofertas | duas linhas na planilha: `KS02006`, `KS03022` |
| **Cadastral** | 549 `UNMAPPED` + 13 candidatos/ambíguos | 387 códigos de canal na fila; de-para não derivável |
| **Estoque** | 22 kits do Full | nenhum ganha `kit_available_units`; ver KITS-MAP-2 §8 |
