# DRE-CMP-SKU-1 — custo unitário CMP por SKU (dry-run)

Status: **`CMP_SKU_DRY_RUN_READY`** — parser, qualidade, temporalidade, cobertura e shadow entregues em dry-run.
A margem completa por produto continua **bloqueada** (seção 9).

Nada foi escrito em banco, API ou frontend. Não há migration, nem modo de escrita no CLI. Este documento traz
**apenas contagens e percentuais**; CMP, receita e margem medidos ficam no relatório local não versionado
(`.local/dre_cmp_sku_1/`).

## 1. Decisões aplicadas (não rediscutidas)

- regra de custo: **CMP**; nenhuma outra coluna o substitui (CMM, SB2, D2, custo de compra);
- comparativos AP, BB, Kokeshi e MAGA: `Filial = SP`; MAGA usa **SP**, nunca MG1/MG2/RJ;
- `CMV-LCV`: `0301` é SP; `0302` é ignorada;
- só custo unitário por SKU: receita, canal, `MKP`, CMV realizado e bonificações não são lidos;
- bonificação entrará depois como premissa percentual sobre a receita.

## 2. Como rodar

```bash
# só as planilhas
python -m pipelines.dre.cmp_sku --pasta dre
# planilhas + cobertura/shadow contra o Data Mart (somente leitura, precisa de VPN)
python -m pipelines.dre.cmp_sku --pasta dre --com-torre --de 2026-01 --ate 2026-09
```

| saída | onde |
|---|---|
| resumo agregado (contagens, sem valores) | stdout (JSON) |
| `relatorio.json`, `candidatos_cmp.csv`, `vendas_classificadas.csv` | `--saida` (padrão `.local/dre_cmp_sku`, ignorado pelo git) |

Códigos de saída: `0` ok · `65` fonte recusada · `69` Torre indisponível · `2` argumento inválido.

Travas do CLI:

- `--pasta` é obrigatória e explícita;
- só abre arquivos do conjunto autorizado (padrões de nome em `pipelines/dre/cmp_sku.py`); o resto é listado
  como ignorado, sem ser aberto (hoje: `DRE mktplace.xlsx`); `--arquivo` com nome fora do conjunto é recusado;
- duas planilhas da mesma entidade na pasta são recusadas;
- SHA-256 de cada arquivo antes e depois da leitura: se mudar, o arquivo é recusado;
- cabeçalho diferente do contrato é recusado: colunas fixas, meses `Mmm/AA` consecutivos e último mês igual
  ao corte do nome do arquivo;
- coluna com `CMM` no cabeçalho do comparativo, ou ausência de `CMP Unit` no CMV-LCV, recusam o arquivo;
- a conexão com o Data Mart usa `SET TRANSACTION READ ONLY` e termina em rollback; nenhuma credencial é
  impressa (`sanitizar` do publisher da DRE).

## 3. Contrato de extração

| arquivo | aba lida | filial | colunas extraídas | validadas e descartadas |
|---|---|---|---|---|
| comparativos AP, BB, Kokeshi, MAGA | `Consolidado CMP` | `SP` | `Filial`, `Cód. Próprio`, `GTIN`, `Descrição`, meses, `Custo Atual` | `Empresa`, `Entradas` |
| `CMV-LCV-<mes>-<ano>` | `Resumo Produto` | `0301` | `Filial`, `Produto`, `Marca`, `CMP Unit`, `Diagnóstico` | `CMM Unit`, `CM1 SB2`, `CustUnit D2`, `Custo *`, `Venda`, `Margem CMM %` |

Abas nunca abertas: `CMV Jul-26`, `Bonificações Jul-26`, `Compras CMP`, `Compras CMM`, `Planilha1`,
`Itens Detalhados`, `Totais por Tipo`, `CMV Intercompany`, `Posição Estoque`.

Cada linha candidata tem: regime (`mensal` ou `fotografia_atual`), competência, vigente em, entidade, marca,
filial, SKU, GTIN, descrição, CMP, arquivo, aba, linha, coluna e SHA-256 do arquivo.

A aba `Consolidado CMP` **não tem coluna de marca**. A marca não foi inferida: ela vem do lado da Torre no
cruzamento. No CMV-LCV, a coluna `Marca` está vazia em todas as linhas `0301`.

## 4. Regime temporal medido

| fonte | regime |
|---|---|
| comparativos | série mensal Jan/25…Jul/26; o valor é o CMP vigente no fim do mês e **se repete até a próxima entrada** |
| CMV-LCV | uma única fotografia da competência do nome do arquivo (jul/2026); um único CMP por produto no mês |

Fatos que sustentam a leitura:

- `Custo Atual` = `Jul/26` em **100%** das linhas SP dos quatro comparativos (959 de 959 com valor);
- **nenhum** zero depois do primeiro CMP (sem buracos na série);
- antes da primeira entrada a célula vem `0`: **ausência, não custo zero** — 5.489 células assim;
- no CMV-LCV, 41 das 87 linhas `0301` têm `CMP Unit = 0` (38 com diagnóstico `Sem entrada SD1`): também
  tratado como ausência.

## 5. Qualidade (linhas SP)

| métrica | valor |
|---|---:|
| linhas lidas (todas as filiais) | 3.062 |
| linhas SP/0301 | 1.048 |
| linhas candidatas (SKU × mês com CMP > 0, mais fotografias) | 13.737 |
| chaves entidade × filial × SKU | 707 |
| SKUs únicos | 701 |
| CMP nulo / negativo / não numérico | 0 / 0 / 0 |
| duplicatas exatas (mesmo valor; contadas, não somadas) | 299 (AP 140 · BB 103 · Kokeshi 38 · MAGA 18 · LCV 0) |
| mesmo SKU com mais de um CMP na mesma entidade e competência | 0 |
| SKUs em mais de uma entidade | 6 |
| SKU × competência com CMP diferente entre entidades | 30 |
| código próprio inválido (`SEM GTIN`) | 2 |
| códigos de marca sem de-para nas linhas SP | 0 (a coluna está vazia) |
| competência mínima / máxima | 2025-01 / 2026-07 |
| fórmulas nas células extraídas | 0 → diferença entre cache e cálculo não se aplica |
| links para pastas de trabalho externas | 3 (AP 2, MAGA 1), referenciados só em abas não lidas |

Filtros salvos escondem 1.552 linhas no AP e 247 na MAGA; o parser ignora a visibilidade e lê todas.
O título da aba diz "até 31/05/2026", mas as colunas vão até Jul/26 — título antigo, não contrato.

## 6. Alternativas temporais

| alternativa | regra |
|---|---|
| 1. por competência | só o CMP do próprio mês; fora da série ou antes da 1ª entrada → ausente |
| 2. último disponível | a fotografia `Custo Atual` para qualquer mês |
| 3. entidade × vigência | intervalos de valor constante por entidade; o último fica aberto após o corte |

Sobre as vendas da Torre de jan–set/2026 (competência × SKU vendido), quantos mudam de resultado:

| par | competência × SKU que mudam |
|---|---:|
| 1 × 2 | 884 |
| 1 × 3 | 364 (todos em ago–set: a 1 não cobre depois do corte) |
| 2 × 3 | 520 (meses antes do corte, onde a 2 aplica um custo futuro) |

Até jul/2026 as alternativas 1 e 3 são idênticas. A 2 atribui custo a vendas anteriores à primeira entrada
do produto e **aumenta conflitos** entre entidades em todos os meses anteriores a julho, porque usa a
fotografia final de cada uma. Em jul/2026 as três coincidem.

## 7. Cruzamento com a Torre

Vendas: `gold.nf_vendas_plataforma_sku_mensal`, `canal = 'marketplace'`, TikTok, ML e Shopee, por emissão de
NF. A receita é **fiscal**, não o GMV da Torre. Pelo MARGEM-REAL-1 §4.2, ela reconcilia com a Torre no
TikTok e **não** reconcilia em ML e Shopee: o recorte por canal desses dois é indicativo.

### 7.1 Achados que mudaram o desenho

1. **Código igual não é produto igual.** Códigos numéricos vêm de ERPs diferentes e colidem entre marcas: o
   código fiscal de uma venda Lescent existe na BB VAREJO como um condicionador Barbours.
2. **O cadastro também colide.** O mesmo código é kit numa marca e produto simples em outra. Sem escopo por
   marca, 165 linhas de venda pareciam kits; com escopo, sobra 1 em nove meses.
3. **O mesmo produto tem dois códigos com dois CMPs na mesma entidade** (BB VAREJO: código numérico antigo e
   código `BB…`). A série do código `BB…` começa em mar/2026 (num caso, em jul/2026); antes disso só o código
   numérico tem CMP. Por isso o conflito aparece a partir de março. É conflito real, e nenhuma regra aprovada
   escolhe entre os dois.

### 7.2 Regra de casamento

- igualdade exata de código (`codigo_sku` ou `sku_canonico` × código da planilha), com cada via registrada;
- cadastro, GTIN e kits sempre escopados pela marca da venda;
- cada candidato passa por duas evidências: **GTIN** (EAN-13 com dígito verificador) e **volume declarado**
  (ml, g, caps). Qualquer divergência **descarta** o candidato;
- o candidato aceito recebe a evidência mais forte que concorda: `GTIN` (forte), `VOLUME` (fraca: uma linha
  inteira de body splash tem 200 ml) ou `INDETERMINADO`;
- dois candidatos aceitos com CMP diferente → `conflito`, sem valor;
- nunca média, mínimo, máximo ou "último".

### 7.3 Cobertura — jul/2026, % da receita fiscal

| canal | coberto | só GTIN | sem CMP | conflito | evidência diverge |
|---|---:|---:|---:|---:|---:|
| TikTok | 64,1 | 14,7 | 16,5 | 19,3 | 0,1 |
| Mercado Livre | 74,9 | 23,5 | 12,8 | 12,3 | 0,0 |
| Shopee | 79,2 | 2,6 | 16,9 | 3,8 | 0,1 |
| **total** | **70,1** | **15,3** | **15,4** | **14,4** | **0,1** |

| marca | coberto | GTIN | VOLUME | INDETERMINADO | sem CMP | conflito |
|---|---:|---:|---:|---:|---:|---:|
| apice | 72,8 | 0,0 | 43,6 | 29,2 | 26,5 | 0,0 |
| barbours | 49,2 | 45,5 | 0,9 | 2,9 | 7,2 | 43,6 |
| kokeshi | 76,6 | 3,5 | 2,3 | 70,7 | 20,5 | 2,9 |
| lescent | 98,8 | 0,0 | 0,0 | 98,8 | 1,2 | 0,0 |
| rituaria | 81,9 | 0,2 | 81,7 | 0,0 | 18,1 | 0,0 |

SKUs vendidos em jul/2026 (marca × código): 320. São 168 cobertos, 143 sem CMP, 7 em conflito e 2 com
evidência divergente. Por tipo no cadastro: 256 simples e 64 desconhecidos (fora do cadastro da marca), sem
nenhum kit no mês.

Leituras:

- **Lescent** está quase toda coberta, mas só pelo CMV-LCV, que não tem GTIN nem descrição: nível
  `INDETERMINADO`. O código `LC…` é próprio da marca, o que reduz, mas não elimina, o risco de colisão.
- **Kokeshi** está 71% em `INDETERMINADO`: na planilha Kokeshi, a descrição costuma ser o próprio código, e
  só 7 das 27 linhas casadas por código têm GTIN.
- **Barbours** é a única marca com GTIN útil; o que falta é a regra para o produto com dois códigos.
- **Apice** se apoia no volume, que é evidência fraca: nenhuma das 39 linhas AP casadas por código tem GTIN.
  A AP tem GTIN em 270 de 342 SKUs, mas em linhas com **outro** código.
- **Rituaria** também depende do volume: o nome da venda não traz EAN.

**Via não aplicada — casamento por GTIN.** Das 100 linhas de venda Apice sem CMP em jul/2026, 92 têm no nome
um EAN que existe como GTIN numa linha SP da AP; na Kokeshi são 19 de 43. Essa via elevaria bem a cobertura,
mas não foi ligada: este projeto já mediu EAN reaproveitado entre tamanhos de embalagem, e casar por GTIN é
uma regra nova que precisa de aprovação e da mesma checagem de volume.

### 7.4 Contraprova de custo

Nas linhas cobertas, `quantidade × CMP` foi comparado ao `custo` que a própria fonte fiscal já carrega
(`fonte_custo = bling_cadastro`, um custo de cadastro sem vigência). A diferença é **sistemática e varia por
marca**: Kokeshi com CMP bem abaixo do cadastro, Lescent acima e as demais um pouco abaixo. Isso não invalida
o CMP, que é a regra decidida, mas mostra que trocar a fonte de custo da DRE muda o CMV de forma material. Os
valores estão no relatório local.

## 8. Kits

- composição disponível: `gold.bridge_kit_componente_gobeauty` e `silver.gobeaute_produto_cadastro`
  (`tipo = 'KT'`), ambos escopados por marca;
- em jan–set/2026 a fonte fiscal tem **1** linha de venda de kit, sem CMP próprio e sem soma de componentes
  calculável;
- [INFERÊNCIA] as NFs de marketplace parecem itemizar componentes, não o kit. Se for verdade, o kit chega à
  DRE já decomposto e a pergunta "CMP do kit × soma dos componentes" perde peso na fonte fiscal. Não está
  provado;
- nada foi escolhido: o shadow exclui kits (`kit_sem_regra`).

## 9. Shadow de margem por produto

Nome do número: **margem bruta após CMP** = `receita fiscal coberta − quantidade × CMP`. Não é margem de
contribuição nem margem líquida: nenhum custo variável está atribuído por SKU.

| custo variável | TikTok | Mercado Livre | Shopee |
|---|---|---|---|
| comissão/tarifa | só dia × marca | item de pedido em `api`, fora do mart | só dia × marca |
| frete | só statement | dia × marca | dia × marca |
| afiliados | só mês × marca | sem fonte | sem fonte |
| ads | inexistente no mart | dia × marca | dia × marca |
| impostos | sem premissa | sem premissa | sem premissa |
| bonificação | premissa % pendente | premissa % pendente | premissa % pendente |

O shadow é calculado com a alternativa 1 (por competência), em dois níveis: só GTIN, e todos os aceitos. Ele
cobre 70,1% da receita fiscal de jul/2026 (15,3% no nível GTIN). Os valores ficam só no relatório local.

**Bloqueios da margem completa:**

1. nenhum custo variável no grão SKU (tabela acima);
2. atribuição de canal da receita fiscal não reconcilia em ML e Shopee;
3. regra temporal do CMP não decidida (a escolha muda 884 competência × SKU);
4. sem precedência entre entidades nem entre códigos do mesmo produto (14,4% da receita de jul em conflito);
5. 15,4% da receita de jul sem CMP;
6. 42,3% da receita coberta sem evidência além do código;
7. bonificação e impostos sem premissa aprovada.

## 10. Perguntas abertas

1. Regra temporal: por competência (1) ou vigência por entidade (3)? A (2) é desaconselhada pelos dados.
2. Qual entidade/código prevalece quando o mesmo produto tem dois CMPs (ex.: BB VAREJO numérico × `BB…`)?
3. Aceitar o casamento `INDETERMINADO` (sem GTIN nem volume) para Lescent/Kokeshi, ou exigir de-para
   explícito código-planilha → SKU da Torre?
4. Ligar a via de casamento por GTIN (Apice/Kokeshi), com checagem de volume obrigatória?
5. As NFs de marketplace itemizam o kit ou os componentes? (decide se a regra de kit é necessária)
6. Os 143 SKUs vendidos sem CMP: existem em outra entidade ou filial fora de SP, ou falta arquivo?

## 11. Reprodução e testes

- código: `pipelines/dre/cmp_sku.py` (planilhas) e `pipelines/dre/cmp_sku_torre.py` (cobertura/shadow);
- testes: `pipelines/tests/test_dre_cmp_sku.py` — XLSX sintéticos, sem banco e sem planilha real.
- base de contexto: a auditoria das planilhas de 13/10/2026 (documento local, ainda não versionado) e o
  `docs/margem_real_1_diagnostico.md`.
