# DRE-PREMISSAS-1 — cenário estimado com premissas por marca

Status: implementado em PR, **não aplicado**. A migration 026 não foi executada, nada foi publicado no Neon e
não houve deploy. Este documento traz só estrutura, regras, estados e coberturas agregadas. Nenhum
percentual real por marca, valor monetário ou margem aparece aqui; esses ficam em relatório local fora do Git.

## 1. O que é

A DRE realizada (`/api/v1/dre`) continua entregando `margem_contribuicao_conhecida` exatamente como antes.
Este gate acrescenta, em cada célula e em cada agregado, um objeto **opcional e complementar**,
`cenario_premissas`, sempre com `natureza = "estimada"` e o rótulo
**"Margem de contribuição estimada com premissas"**. Ele completa as linhas da DRE que não têm fonte
realizada (impostos, devoluções, bonificação, embalagem, publicidade, logística etc.) com percentuais de
orçamento por marca.

As duas margens nunca se misturam: nomes, campos e rótulos são diferentes.

## 2. Fonte das premissas

Matriz privada (XLSX, ignorada pelo Git) com uma aba, as marcas em C:K e as linhas da DRE na coluna A.
O comentário de C1 declara o arquivo de orçamento de origem, a data de envio e a coluna de referência.
A competência é tirada desse comentário, e a primeira matriz declara **out/26 → 2026-10**.

**Privacidade da procedência.** O nome do arquivo de orçamento existe só no manifesto privado do
publicador. Banco, API e stdout expõem apenas o rótulo "Premissas de outubro/2026", a competência, a data da
fonte e o SHA-256.

| item | contrato |
|---|---|
| marcas consumidas | `kokeshi`, `apice`, `barbours`, `rituaria`, `lescent` (obrigatórias, uma vez cada) |
| marcas fora do escopo | BySamia, Denavita, Aua, Yenzah: ignoradas e contadas; nunca carregadas nem zeradas |
| marca desconhecida | recusa o arquivo |
| linhas de premissa | 18, identificadas pelo **rótulo** (nunca pela cor nem pela posição) |
| célula consumida | número entre 0 e 1, ou vazia (= ausência). Fórmula, texto ou booleano recusam o arquivo |
| zero | premissa explícita de 0% |
| coluna B | nunca lida (são fórmulas do modelo); nenhum cache de fórmula é usado |
| linhas alimentadas pela Torre | Receita, CMV, Mídia Online, Influencer, Despesas de Marketplace e subtotais precisam estar vazias em C:K |
| integridade | SHA-256 igual ao `--expected-sha256`, conferido antes e depois da leitura |

Componentes (tabela 026): `devolucoes_reembolsos`, `icms`, `difal`, `pis`, `cofins`, `iss_ipi_simples`,
`outras_receitas_operacionais`, `frete_primario`, `bonificacoes`, `embalagens`, `provisao_perda_estoque`,
`publicidade_propaganda`, `comissoes`, `agencia_growth`, `despesas_gateway`, `brindes`, `logistica_fretes`,
`logistica_operador`.

**Sinal não ratificado.** "Outras Receitas Operacionais" tem rótulo de receita, mas a fórmula da matriz a
trata como dedução. Ela é armazenada com `somavel=false` e `status='sinal_nao_ratificado'`, fica fora da
margem e aparece como linha informativa. Nenhum sinal é escolhido.

**Sobreposição não ratificada.** "Comissões" pode se sobrepor às taxas reais de marketplace, que a DRE já
usa. A taxa é armazenada e exibida (percentual e valor na célula), mas com `somavel=false` e
`status='sobreposicao_nao_ratificada'`, e fica fora da margem até a ratificação.

## 3. Armazenamento — migration 026

`marts.fact_dre_premissa_mensal`, com grão `competencia × brand × component`. Colunas: `competencia`,
`brand`, `component`, `rate`, `somavel`, `status`, `source_period`, `source_sha256`, `source_note`,
`source_sent_on`, `loaded_at` e `run_id` (FK para `audit.source_sync_run`).

| CHECK | garante |
|---|---|
| PK no grão | uma taxa por competência × marca × componente |
| `rate` em 0..1 e `<> 'NaN'` | faixa; `'NaN'::numeric >= 0` é TRUE no PostgreSQL |
| `rate IS NULL` ⇔ `status = 'ausente_na_origem'` | ausência explícita, nunca zero |
| allowlist de marcas e componentes | nenhum publisher grava o que o serving não conhece |
| `somavel = false` ⇔ `outras_receitas_operacionais` ou `comissoes`, cada um com seu status | premissas não ratificadas nunca entram na soma |
| `source_sha256` hexadecimal de 64 caracteres | procedência verificável |
| `source_note` no formato `Premissas de <mês>/<aaaa>` | nunca o nome do arquivo de orçamento nem um caminho local |

Não há coluna monetária, e a migration não insere dados. A reversão é `alembic downgrade 025`, que derruba só
esta tabela.

## 4. Publicador — `python -m pipelines.dre.premissa_publisher`

```bash
python -m pipelines.dre.premissa_publisher \
  --arquivo <matriz privada> --competencia 2026-10 --expected-sha256 <sha> [--sem-banco] [--apply]
```

- dry-run por padrão (lê o Neon em `READ ONLY` só para comparar); `--sem-banco` nem consulta o banco;
- `--apply`: transação única com `pg_try_advisory_xact_lock(918140026)`, sem espera nem retry;
- backup das linhas do escopo **antes** do DELETE, num arquivo privado (`--manifesto-dir`, padrão
  `.local/dre_premissas`);
- o DELETE só alcança `competencia × cinco marcas`. A contagem fora do escopo é conferida antes e depois; se
  mudar, há rollback;
- reconciliação pós-escrita: o que se lê de volta tem de ser igual ao que foi montado;
- auditoria em `audit.source_sync_run` (`source_name = dre_premissa_mensal`). A falha é registrada em
  transação própria;
- manifesto privado sem taxas, sem caminho local e sem credencial.

Códigos de saída: `0` ok · `65` matriz/escrita recusada · `69` banco indisponível · `75` lock ocupado ·
`78` migration ausente.

## 5. Cálculo

Sempre na célula base `competência × canal × marca`:

`valor_premissa = receita_bruta_da_célula × taxa_da_marca`

Canal, marca e total são **somas de células**. Nenhuma taxa é reaplicada depois da agregação, e o agregado
traz `percentual = null` e só o efetivo `pct_receita`.

| # | linha | origem |
|---|---|---|
| 1 | Receita Bruta | realizado (receita servida pela DRE) |
| 2 | (-) Devoluções e Reembolsos | premissa |
| 3 | (-) Impostos sobre Receita: ICMS, DIFAL, PIS, COFINS, ISS/IPI/Simples | premissa (subtotal calculado) |
| 4 | (=) Receita Líquida Estimada | calculado |
| 5 | (-) Custo das Mercadorias | realizado (CMV atual da DRE, com status) |
| 6–9 | (-) Frete Primário, Bonificações, Embalagens, Provisão para Perda de Estoque | premissa |
| 10 | (=) Lucro Bruto Estimado | calculado |
| 11 | (-) Mídia Online | realizado (Ads da DRE) |
| 12 | (-) Influencer | decomposição (TikTok) / indisponível |
| 13 | (-) Publicidade e Propaganda | premissa |
| 14 | (-) Comissões | premissa **exibida, fora da soma** (`sobreposicao_nao_ratificada`) |
| 15–16 | (-) Agência de Growth, Gateway | premissa |
| 17 | (-) Despesas de Marketplace | realizado ou decomposição (TikTok) |
| 18–20 | (-) Brindes, Logística — Fretes, Logística — Operador | premissa |
| 21 | (=) Margem de Contribuição Estimada | calculado |
| — | Outras Receitas Operacionais | informativa, fora da soma (`sinal_nao_ratificado`) |
| — | Frete realizado (comparação) | informativa, nunca somada ("Logística — Fretes" vem da matriz) |

**Afiliado TikTok.** Ele já está dentro das taxas do repasse. Quando a decomposição é segura (afiliado
medido e menor ou igual à taxa):
- `despesas_marketplace = taxas − afiliados`;
- `influencer = afiliados`;
- os dois somam exatamente as taxas.

Caso contrário, a taxa entra integral e o influencer fica só informativo (`somavel=false`). No Mercado Livre
o influencer é `nao_aplicavel`; na Shopee fica indisponível ou não confirmado e nunca é somado.

**CMV.** Vem da DRE atual, com o status, a provisoriedade e a reconciliação que ela já tem
(`fonte_cmv = "dre_realizada"`). O contrato já aceita `"cmp_sku"` (PR #78), mas ainda não o usa.

**Competência exata.** A premissa vale só para a competência publicada: setembro não recebe a de outubro, e
novembro não herda outubro. Célula sem premissa → `cenario_premissas = null`. Um agregado que mistura células
com e sem premissa → `nao_calculavel`.

## 6. Estados

| estado | quando |
|---|---|
| `calculavel` | receita, CMV, taxas e Ads aplicável utilizáveis; todas as premissas presentes; nenhuma regra de dupla contagem falhou; competência madura (M+2) e encerrada |
| `parcial` | há margem sobre os componentes conhecidos, mas: mês em andamento (**prévia**), competência imatura, receita parcial, CMV provisório/parcial, taxa parcial ou não confirmada, Ads ausente, premissa ausente ou decomposição TikTok insegura |
| `nao_calculavel` | falta receita, CMV utilizável ou taxa utilizável, ou o lote de premissas é inválido (não concluído, misto ou de mais de uma fonte). `margem_contribuicao_estimada = null` |

Zero nunca é fabricado para tornar o cenário calculável. A regra de maturidade da DRE realizada não muda; a
célula realizada de outubro continua `NAO_CALCULAVEL`.

**Maturidade não apaga o valor estimado.** Com receita, CMV e taxas presentes, outubro em andamento devolve
`margem_contribuicao_estimada` com valor, `estado = parcial` e `previa = true`. Isso vale também nos
agregados. Ao mesmo tempo, `margem_contribuicao_conhecida` continua `null` e a célula realizada continua
`NAO_CALCULAVEL` pela regra M+2. Ambos os pontos são provados por teste.

## 7. Contrato da API (aditivo)

Campos novos, todos opcionais:

- `fontes.premissas_dre`:
  - `estado`: `ok` · `parcial` · `tabela_ausente` · `nao_publicado` · `lote_invalido` · `nao_consultado`;
  - `competencias_publicadas`, `source_sha256`, `rotulo`, `data_fonte`, `carregado_em`, `motivo`.
- `regras.rotulo_margem_estimada` e `regras.contrato_premissas`.
- `cenario_premissas` em `total`, `por_canal[]`, `por_marca[]`, `por_canal_marca[]` e `celulas_base[]`:

```text
competencia_premissas  "2026-10"
natureza               "estimada"
rotulo                 "Margem de contribuição estimada com premissas"
estado                 calculavel | parcial | nao_calculavel
previa                 true quando a competência está em andamento
competencia_madura     regra M+2 da DRE realizada
receita_base           receita bruta da célula (ou soma, no agregado)
fonte_cmv              "dre_realizada"
linhas[]               linha, rotulo, tipo (item|subtotal|informativo), valor, pct_receita,
                       origem (realizado|premissa|decomposicao|indisponivel|calculado|misto),
                       percentual (só na célula base), status, motivo, somavel
componentes_ausentes[] linha, motivo
motivos[], avisos[]
margem_contribuicao_estimada, margem_contribuicao_estimada_pct
fonte_premissas        rotulo ("Premissas de outubro/2026"), competencia, data_fonte, source_sha256,
                       carregado_em — nunca nome de arquivo ou caminho
celulas                número de células base somadas
```

A API lê **só o Neon** (nunca o XLSX). Ela continua respondendo 200 quando a tabela 026 não existe e quando a
competência não foi publicada, e o payload antigo continua idêntico (provado por teste).

## 8. Cobertura medida (dry-run privado, outubro/2026, somente leitura)

A matriz real foi lida com o hash conferido e não foi alterada: 90 linhas (5 marcas × 18 componentes), 85
`ok` e 5 `sinal_nao_ratificado`, 0 ausências, 4 marcas ignoradas.

O cenário de outubro foi montado em memória com dados do Neon (leitura `READ ONLY`, sem publicar nada):

| recorte | células | calculável | parcial | não calculável |
|---|---:|---:|---:|---:|
| total (canal × marca) | 14 | 0% | 0% | 100% |
| por canal (TikTok, ML, Shopee) | 3 | 0 | 0 | 3 |
| por marca | 5 | 0 | 0 | 5 |

Motivo comum às 14 células: **o CMV de outubro não está publicado** em `marts.fact_dre_insumo_mensal`. O
publisher de insumos só publica meses fechados. Também pesam o repasse TikTok ausente (5 células), Ads TikTok
sem fonte (5) e a prévia (14). A receita e as premissas estão disponíveis em 100% das células. Pelo contrato,
nada disso vira zero.

## 9. Integração com o frontend (handoff para o PR #77)

O PR #77 (`/dre`) ainda está aberto, por isso este gate **não toca o frontend**. Contrato para quem mantém
o #77:

1. alternância explícita **"Conhecida"** × **"Estimada com premissas"**; nunca as duas margens sem rótulo;
2. no modo estimado, cabeçalho vindo de `cenario_premissas.fonte_premissas.rotulo` ("Premissas de outubro/2026"),
   com a data da fonte e o SHA-256 como detalhe;
3. selo **"Prévia"** quando `previa = true`;
4. coluna de origem por linha (`realizado`, `premissa`, `decomposição`, `indisponível`);
5. comparação conhecido × estimado lado a lado, com a mesma receita base;
6. lista de `componentes_ausentes` e os avisos de linhas fora da margem: "Outras Receitas Operacionais"
   (`sinal_nao_ratificado`) e "Comissões" (`sobreposicao_nao_ratificada`). As duas aparecem como premissa,
   mas sem entrar na soma;
7. `cenario_premissas = null`: mostrar "Sem premissas para esta competência" e usar `fontes.premissas_dre.motivo`
   para explicar se a tabela não existe ou se a competência não foi publicada;
8. filtros de marca, canal e competência preservados (o cenário segue o mesmo recorte da DRE);
9. `valor = null` nunca é exibido como "R$ 0".

## 10. Próximos passos (cada um exige autorização explícita)

1. Revisar e mesclar este PR.
2. `alembic upgrade 026` no Neon.
3. Publicar outubro: rodar o comando da seção 4 em dry-run e conferir o manifesto; depois o mesmo com `--apply`.
4. Deploy da API no Render.
5. Integrar a tela no #77, conforme a seção 9.
6. Para o cenário passar de `nao_calculavel`: CMV e repasse de outubro publicados pela ponte da DRE (depois do
   fechamento do mês). Para virar `calculavel`: maturidade M+2 e Ads TikTok, ou aceitar o cenário como
   `parcial`.

## 11. Pendências de negócio

- sinal de "Outras Receitas Operacionais" (receita ou dedução);
- possível sobreposição entre a premissa "Comissões" e as taxas reais de marketplace: hoje ela fica fora da
  margem (`somavel=false`). Volta à soma só depois de ratificada;
- vigência: a premissa de outubro não se estende. Novembro precisa de matriz própria.
