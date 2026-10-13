# DRE-CMP-SKU-1/2 — custo unitário CMP por SKU (dry-run)

Status: **`CMP_SKU_DRY_RUN_READY`**. Parser, qualidade, temporalidade, cobertura e shadow foram entregues em
dry-run, já com as decisões de 13/10/2026 aplicadas (DRE-CMP-SKU-2). A margem completa por produto continua
**bloqueada** (seção 9).

Nada foi escrito em banco, API ou frontend. Não há migration nem modo de escrita no CLI. Este documento traz
**apenas contagens e percentuais**. CMP, receita, CMV e margem medidos ficam no relatório local não versionado
(`.local/dre_cmp_sku_1/`).

## 1. Decisões aplicadas (não rediscutidas)

Fontes (DRE-CMP-SKU-1):

- regra de custo: **CMP**; nenhuma outra coluna o substitui (CMM, SB2, D2, custo de compra);
- comparativos AP, BB, Kokeshi e MAGA: `Filial = SP`. Na MAGA vale **SP**, nunca MG1/MG2/RJ;
- `CMV-LCV`: `0301` é SP; `0302` é ignorada;
- só custo unitário por SKU: receita, canal, `MKP`, CMV realizado e bonificações não são lidos;
- bonificação entrará depois como premissa percentual sobre a receita.

Shadow (DRE-CMP-SKU-2):

- **temporalidade = alternativa 3, entidade × vigência.** O CMP vale a partir da competência em que aparece e
  segue vigente até nova entrada da mesma entidade/filial/SKU. Nunca retroage e nunca usa `Custo Atual`.
  A fotografia do CMV-LCV vale a partir da própria competência e, adiante dela, fica marcada como
  "fotografia carregada";
- **Barbours**: não há precedência automática entre o código numérico e o `BB…`. Dois candidatos válidos com
  CMP diferente ficam `conflito`;
- **código exato sem outra evidência** = `CODIGO_EXATO_PROVISORIO`, de baixa confiança. Exige escopo de
  marca/entidade/filial, candidato único e nenhuma divergência;
- **via GTIN** ativada só com GTIN válido e único, escopo, candidato único, volume compatível e nenhum reuso
  de EAN entre volumes. Código exato tem precedência, e a via GTIN nunca resolve conflito;
- **`KS03041`/`KS03042`** seguem sem CMP (`origem_a_confirmar`).

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
| `relatorio.json`, `candidatos_cmp.csv`, `vendas_classificadas.csv`, `depara_temporal_necessario.csv` | `--saida` (padrão `.local/dre_cmp_sku`, ignorado pelo git) |

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


**Decisão (13/10/2026): alternativa 3.** A tabela acima foi medida na v1, antes das regras de casamento da
seção 7, e fica como registro de por que a 2 foi descartada.

Cada casamento agora registra a competência de origem (último mês que a fonte declara até a venda), a
competência usada (mês da venda) e se houve carry-forward. Carry-forward = valor usado num mês que a fonte não
declara: depois do corte da série (ago–set/2026) ou depois do mês da fotografia do CMV-LCV.

## 7. Cruzamento com a Torre

Vendas: `gold.nf_vendas_plataforma_sku_mensal`, `canal = 'marketplace'`, TikTok, ML e Shopee, por emissão de
NF. A receita é **fiscal**, não o GMV da Torre. Pelo MARGEM-REAL-1 §4.2, ela reconcilia com a Torre no
TikTok e **não** reconcilia em ML e Shopee: o recorte por canal desses dois é indicativo.

### 7.1 Achados que moldaram as regras

1. **Código igual não é produto igual.** Códigos numéricos vêm de ERPs diferentes e colidem entre marcas: o
   código fiscal de uma venda Lescent existe na BB VAREJO como um condicionador Barbours.
2. **O cadastro também colide.** O mesmo código é kit numa marca e produto simples em outra. Sem escopo por
   marca, 165 linhas de venda pareciam kits; com escopo, sobra 1 em nove meses.
3. **O mesmo produto tem dois códigos com dois CMPs na mesma entidade** (BB VAREJO: código numérico antigo e
   código `BB…`). A série do código `BB…` começa em mar/2026 (num caso, em jul/2026). O mesmo padrão aparece
   em 1 produto Kokeshi.
4. **O mesmo GTIN aparece em vários códigos com CMPs diferentes** dentro da mesma entidade, por exemplo em
   linhas de kit ou com prefixo `GGTIN:`. Isso responde por quase toda a ambiguidade da via GTIN: 155 de 159
   casos ambíguos têm candidatos com CMP diferente.

### 7.2 Regra de casamento

Escopo: só as entidades da marca da venda e a filial SP. **[ASSUNÇÃO a ratificar]** O de-para marca →
entidade vem da auditoria de 13/10:

| marca | entidades no escopo |
|---|---|
| apice | AP_COSMETICS, KOKESHI |
| barbours | BB_VAREJO |
| kokeshi | KOKESHI |
| lescent | AP_COSMETICS, BB_VAREJO, LCV |
| rituaria | MAGA_COMERCIO |

`denavita` não tem entidade nas planilhas e fica 100% sem CMP.

Ordem de resolução:

1. `KS03041`/`KS03042` → `origem_a_confirmar`. Nenhuma busca em outra filial, entidade ou marca;
2. **código exato** (`codigo_sku` ou `sku_canonico`), com a vigência da alternativa 3. Cada candidato passa por
   GTIN e por volume, e qualquer divergência o descarta:
   - candidatos válidos com CMP diferente → `conflito` (nada escolhe: nem GTIN, alias, média, mínimo, máximo
     ou o mais recente);
   - GTIN concorda → `CODIGO_EXATO_GTIN`;
   - só o volume concorda → `CODIGO_EXATO_VOLUME`;
   - nenhuma evidência e candidato único → `CODIGO_EXATO_PROVISORIO`; mais de um candidato → `ambiguo`;
3. **via GTIN**, só se o código não achou candidato válido → `VIA_GTIN`. A recusa sempre registra o motivo:
   GTIN ausente ou ambíguo na venda, reuso entre volumes, nenhum candidato no escopo, mais de um candidato,
   ou volume divergente ou ausente.

A cobertura **confirmada por GTIN** soma `CODIGO_EXATO_GTIN` e `VIA_GTIN`. O nível provisório nunca entra
nela.

### 7.3 Cobertura — jan–set/2026, % da receita fiscal

| mês | coberto | GTIN (código) | via GTIN | código + volume | provisório | carry-forward | sem CMP | KS a confirmar | conflito | evidência diverge |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 2026-01 | 75,8 | 57,9 | 4,1 | 13,0 | 0,7 | 0,0 | 22,0 | 0,0 | 0,0 | 2,2 |
| 2026-02 | 85,5 | 71,0 | 3,3 | 10,0 | 1,2 | 0,0 | 13,3 | 0,0 | 0,1 | 1,1 |
| 2026-03 | 47,5 | 28,1 | 2,7 | 15,5 | 1,2 | 0,0 | 22,1 | 0,0 | 28,2 | 2,1 |
| 2026-04 | 39,2 | 2,7 | 2,2 | 29,9 | 4,4 | 0,0 | 55,2 | 0,2 | 0,0 | 5,4 |
| 2026-05 | 45,5 | 11,7 | 2,2 | 24,2 | 7,3 | 0,0 | 45,1 | 0,3 | 5,1 | 3,9 |
| 2026-06 | 47,6 | 22,6 | 1,9 | 17,8 | 5,4 | 0,0 | 35,3 | 1,6 | 12,7 | 2,8 |
| 2026-07 | 70,8 | 15,3 | 0,7 | 12,5 | 42,3 | 0,0 | 8,5 | 6,3 | 14,4 | 0,0 |
| 2026-08 | 70,4 | 13,8 | 1,2 | 13,2 | 42,2 | 70,4 | 13,6 | 5,4 | 10,5 | 0,0 |
| 2026-09 | 71,3 | 8,9 | 0,7 | 13,4 | 48,4 | 71,3 | 15,7 | 4,1 | 8,9 | 0,1 |
| **jan–set** | **64,3** | **26,5** | **2,0** | **15,1** | **20,7** | **19,8** | **21,7** | **2,5** | **10,2** | **1,5** |

`ambiguo` e `kit_sem_regra` ficaram em 0,0% em todos os meses. Ago–set são 100% carry-forward: as planilhas
acabam em jul/2026. A fotografia do CMV-LCV carregada adiante responde por 10,3% (ago) e 11,0% (set) da
receita.

| canal (jan–set) | coberto | GTIN (código) | via GTIN | código + volume | provisório | carry-forward | sem CMP | KS | conflito | diverge |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| TikTok | 63,2 | 32,9 | 1,8 | 7,7 | 20,8 | 21,0 | 20,6 | 2,7 | 12,9 | 0,6 |
| Mercado Livre | 67,7 | 19,6 | 2,2 | 29,3 | 16,6 | 19,5 | 20,5 | 1,5 | 6,8 | 3,6 |
| Shopee | 61,8 | 12,2 | 2,2 | 19,1 | 28,3 | 15,6 | 28,8 | 3,5 | 5,0 | 0,9 |

| marca (jan–set) | coberto | GTIN (código) | via GTIN | código + volume | provisório | carry-forward | sem CMP | KS | conflito | diverge |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| apice | 67,4 | 0,0 | 0,0 | 40,1 | 27,2 | 27,9 | 32,4 | 0,0 | 0,0 | 0,3 |
| barbours | 70,4 | 65,4 | 3,8 | 0,4 | 0,9 | 8,7 | 3,5 | 0,0 | 26,0 | 0,0 |
| kokeshi | 51,3 | 5,6 | 1,6 | 1,5 | 42,7 | 28,1 | 40,1 | 7,5 | 1,0 | 0,0 |
| lescent | 53,8 | 0,0 | 0,0 | 0,0 | 53,8 | 40,1 | 26,8 | 0,0 | 0,0 | 19,5 |
| rituaria | 87,6 | 0,2 | 0,0 | 87,4 | 0,0 | 15,6 | 12,4 | 0,0 | 0,0 | 0,0 |

Leituras:

- **Lescent** só tem CMP a partir de jul/2026, porque o CMV-LCV é uma fotografia de julho e a vigência não
  retroage. Antes de julho, o código numérico da venda cai em produtos de outra marca e o candidato é
  descartado (19,5% de evidência divergente). Todo o restante é provisório.
- **Kokeshi** é majoritariamente provisória: a descrição da planilha costuma ser o próprio código.
- **Barbours** tem a melhor evidência (GTIN), mas 26,0% da receita de jan–set está em conflito entre o código
  numérico e o `BB…`.
- **Apice** e **Rituaria** se apoiam no volume, que é evidência fraca.

### 7.4 Antes × depois (cobertura total, % da receita fiscal)

"Antes" é a v1: alternativa 1, sem escopo de entidade, sem via GTIN e com o provisório chamado
`INDETERMINADO`. "Depois" é a v2.

| mês | antes | depois | Δ p.p. |
|---|---:|---:|---:|
| 2026-01 | 71,7 | 75,8 | +4,1 |
| 2026-02 | 82,3 | 85,5 | +3,3 |
| 2026-03 | 44,8 | 47,5 | +2,7 |
| 2026-04 | 37,0 | 39,2 | +2,2 |
| 2026-05 | 43,3 | 45,5 | +2,2 |
| 2026-06 | 45,7 | 47,6 | +1,9 |
| 2026-07 | 70,1 | 70,8 | +0,7 |
| 2026-08 | 0,0 | 70,4 | +70,4 |
| 2026-09 | 0,0 | 71,3 | +71,3 |
| **jan–set** | **42,7** | **64,3** | **+21,6** |

| marca | antes | depois | Δ p.p. |
|---|---:|---:|---:|
| apice | 39,5 | 67,4 | +27,9 |
| barbours | 58,5 | 70,4 | +11,9 |
| kokeshi | 21,7 | 51,3 | +29,6 |
| lescent | 13,7 | 53,8 | +40,1 |
| rituaria | 72,0 | 87,6 | +15,6 |

| canal | antes | depois | Δ p.p. |
|---|---:|---:|---:|
| TikTok | 40,7 | 63,2 | +22,5 |
| Mercado Livre | 46,1 | 67,7 | +21,6 |
| Shopee | 44,4 | 61,8 | +17,4 |

Quase todo o ganho vem do carry-forward de ago–set. Dentro da série (jan–jul), o ganho é de +0,7 a +4,1 p.p.
e vem da via GTIN.

### 7.5 Números pedidos

- **SKUs resolvidos pela via GTIN:** 52 SKUs distintos (Barbours 39, Kokeshi 8, Apice 5), que somam 2,0% da
  receita de jan–set.
- **Por que a via GTIN não resolveu mais:** as recusas, por fração da receita sem casamento, são GTIN ambíguo
  (a maior), nenhum candidato no escopo, falta de volume para afastar reuso, GTIN ambíguo na própria venda,
  GTIN ausente e volume divergente.
- **Barbours ainda bloqueada:** 6 SKUs em conflito, 26,0% da receita Barbours de jan–set e 43,6% em jul.
  Há ainda 1 produto Kokeshi no mesmo padrão. A lista local para de-para temporal está em
  `depara_temporal_necessario.csv`.
- **`KS03041`/`KS03042`:** 7,5% da receita Kokeshi de jan–set e 2,5% da receita total. Em jul/2026 são 6,3%
  da receita total, e 13,8% da Kokeshi.

### 7.6 Origem empresarial de KS03041 e KS03042 — a confirmar

Os dois SKUs não têm CMP na planilha Kokeshi SP. No CMV-LCV, os códigos `KS…` aparecem na filial `0302`,
excluída por decisão. Por decisão, nenhuma outra filial, entidade ou marca é consultada. Falta confirmar
qual empresa compra esses itens e em qual arquivo está o CMP deles.

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

O shadow usa a alternativa 3 e é apresentado por nível: confirmado por GTIN, código + volume, provisório e o
total aceito. Conflitos, ambiguidades, ausências e `KS` ficam fora do valor e visíveis na cobertura. Os
valores só existem no relatório local.

**Bloqueios da margem completa:**

1. nenhum custo variável no grão SKU (tabela acima);
2. atribuição de canal da receita fiscal não reconcilia em ML e Shopee;
3. conflito entre códigos do mesmo produto sem de-para temporal: 10,2% da receita de jan–set;
4. 21,7% da receita de jan–set sem CMP, mais 2,5% de `KS` a confirmar;
5. 20,7% da receita de jan–set depende só do código (provisório);
6. ago–set dependem inteiramente do carry-forward: faltam as planilhas desses meses;
7. bonificação e impostos sem premissa aprovada.

## 10. Decisões externas ainda necessárias

1. Ratificar o de-para marca → entidade da seção 7.2 (hoje é uma assunção derivada da auditoria).
2. De-para temporal para os 7 produtos em conflito (6 Barbours e 1 Kokeshi): qual série vale em cada mês.
3. Origem empresarial e arquivo de CMP de `KS03041`/`KS03042`.
4. Regra para GTIN repetido em vários códigos da mesma entidade com CMP diferente (kits e `GGTIN:`): hoje
   nunca casa.
5. Critério para promover o casamento `CODIGO_EXATO_PROVISORIO` (Lescent, Kokeshi): de-para explícito ou
   aceite formal.
6. Planilhas de CMP de ago/set/2026 no mesmo layout, para encerrar o carry-forward.
7. Se as NFs de marketplace itemizam kits (decide se a regra de kit é necessária).

## 11. Reprodução e testes

- código: `pipelines/dre/cmp_sku.py` (planilhas e vigência) e `pipelines/dre/cmp_sku_torre.py`
  (casamento, cobertura e shadow);
- testes: `pipelines/tests/test_dre_cmp_sku.py` — XLSX sintéticos, sem banco e sem planilha real;
- base de contexto: a auditoria das planilhas de 13/10/2026 (documento local, ainda não versionado) e o
  `docs/margem_real_1_diagnostico.md`.
