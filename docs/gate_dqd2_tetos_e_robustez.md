# Gate DQ-D2 — tetos D−1, robustez do `full_daily` e lacuna do PMA

Engenharia e diagnóstico **somente leitura**. Nenhuma carga, nenhuma escrita
operacional, nenhuma migração, nenhuma dependência nova. Data: 2026-09-08.

## 1. Teto D−1 no incremental Regional — IMPLEMENTADO

O `--incremental` do loader Regional não tinha limite superior: lia
`MAX(date)` já carregado por marketplace como piso e ia até onde a fonte
alcançasse. Com `gold.ml_gestao_diaria` em D0, publicar trazia o dia aberto.

Agora `resolve_incremental_date_to()` resolve o teto em
`America/Sao_Paulo` via `last_closed_date()`:

| Entrada | Resultado |
|---|---|
| `--date-to` ausente | teto = D−1 |
| `--date-to` = D−1 | aceito |
| `--date-to` anterior a D−1 | aceito |
| `--date-to` = D0 ou futuro | `IncrementalCeilingError`, **antes de conectar** |
| `--date-to` de tipo errado | recusado sem ecoar o valor |

O teto entra no SQL dos dois marketplaces como limite **inclusivo**
(`order_date <= teto`, `date_created::date <= teto`).

### Máximo elegível real, não `min()` (corrigido no DQ-D2-R)

A primeira versão deste teto decidia por `min(max_date_source, teto)`. **Isso
está errado quando a fonte tem lacunas**, e o erro é de anunciar uma data que
não existe:

| | |
|---|---|
| teto (D−1) | 07/09 |
| Gold | 05/09 |
| dias que a fonte realmente tem | 05/09 e 08/09 — **nada** em 06/09 nem 07/09 |
| `MAX` global da fonte | 08/09 |
| `min(08/09, 07/09)` | **07/09** — um dia sem uma única linha na fonte |
| máximo elegível real | **05/09** |

Cada consulta de máximo passou a devolver **dois** valores numa única ida ao
banco, e a distinção é de propósito:

- **`max_date_source_global`** — `MAX` sem teto. **Somente observabilidade**:
  aparece no diagnóstico para diferenciar "fonte parada" de "fonte cheia de
  dia aberto", e **nunca autoriza publicação**.
- **`max_date_source_eligible`** — `MAX` real das linhas com data `<= :date_to`,
  medido pelo banco via `FILTER`, **nunca derivado por aritmética**. É o único
  que decide carga, janela e no-op.

`max_date_source_eligible` **pode ser `None`**, e `None` significa "nada
elegível" — não "carregar D−1". Uma fonte inteiramente em D0/futuro resulta em
ausência elegível e **NO_OP**, sem tentar fabricar D−1. NO_OP é resultado
legítimo, não falha: não há dia fechado novo para carregar.

O SQL conceitual, para ML e Shopee, na mesma consulta:

```sql
SELECT MAX(<coluna>::date)                                             AS max_global,
       MAX(<coluna>::date) FILTER (WHERE <coluna>::date <= %(date_to)s) AS max_eligible
  FROM <fonte>
```

Parametrizado, um `SELECT` por marketplace, sem round-trip extra.

### Tipo exato de `date_to`

A validação anterior usava `isinstance(date_to, date)`, que **aceita
`datetime`** — `datetime` herda de `date`. Hora e fuso entrariam numa
comparação de dia fechado. Agora é `type(date_to) is not date`: `date` exato,
`datetime` recusado antes de abrir conexão, mensagem que nomeia só o tipo
recebido e nunca ecoa o valor.

### Reconciliação na mesma janela do staging (corrigido no DQ-D2-R2)

O teto tinha sido aplicado ao staging e **não** à reconciliação de GMV.
Assimetria exata:

| | Janela |
|---|---|
| `_ml_incremental_select` / `_shopee_incremental_select` | `min_date < data <= date_to` |
| `_ml_gmv_source_recalc_incremental` / `_shopee_gmv_source_recalc_incremental` | `data > min_date` — fronteira superior **ABERTA** |

E `execute_incremental_load` chamava as duas reconciliações sem o teto.

Consequência, exatamente no cenário que motivou o teto: com D0 na fonte, o
staging ia até D−1 e o GMV recalculado da fonte incluía D0. A comparação
acusava divergência **falsa** e a carga fazia **rollback** — o teto tornava a
carga impossível em vez de segura.

As duas funções passaram a receber `date_to` **obrigatório e sem default**
(um default permitiria a volta silenciosa da fronteira aberta) e aplicam
`data > min_date AND data <= date_to`. `execute_incremental_load` passa o
mesmo `teto` já resolvido pela execução — não `date.today()`, não o máximo
global, não valor sintetizado.

Fórmulas de GMV, população elegível (`status = 'paid'` no ML, exclusão por
`order_status ILIKE '%cancel%'` no Shopee), primeira carga, refresh/restore da
janela Shopee e o comportamento append-only ficaram **inalterados**.

**Varredura das outras validações do incremental:** `SQL_VALIDATE_ROWCOUNT`,
`SQL_VALIDATE_DUPLICATES`, `SQL_VALIDATE_NULLS`,
`SQL_VALIDATE_NUMERATOR_DENOMINATOR`, `SQL_SHOPEE_GMV_STAGING` e
`SQL_ML_GMV_STAGING` leem `stg_marketplace_region_daily`, que já nasce
limitada pela janela — não precisam de teto. `SQL_TIKTOK_ROWS_CHECK` lê a Gold
pós-insert e é corretamente irrestrito: afirma que TikTok **nunca** aparece,
não uma propriedade de janela. As duas reconciliações de GMV eram as únicas
que leem a FONTE no caminho incremental, e eram as duas defeituosas.

**Fora de escopo, registrado:** `SQL_SHOPEE_GMV_SOURCE_RECALC` e
`SQL_ML_GMV_SOURCE_RECALC` (sem `_incremental`) leem a fonte inteira, sem piso
e sem teto, e são usados **exclusivamente** por `execute_first_load`. A
primeira carga portanto não tem teto D−1 nenhum. Não foi alterado por
instrução expressa do gate; fica como ponto aberto.

`--date-from` continua recusado no incremental: o piso é o `MAX(date)` já
carregado por marketplace, nunca um parâmetro. A carga segue **append-only**
— nenhum `DELETE`, `TRUNCATE` ou `UPDATE`; a idempotência vem da subida do
watermark.

**Código incluído neste gate, ainda NÃO executado operacionalmente.** O
bloqueio do Regional em 2026-09-01 só cai quando a carga for de fato rodada —
o que não aconteceu aqui: nenhuma carga, nenhum `--apply`, nenhum diagnose
contra banco gravável.

## 2. Teto em produtos/ranking ML — **NO-GO**, com prova

`WHERE last_sale <= D−1` era a solução óbvia. Está **proibida**, e a medição
mostra que a proibição do gate estava certa nos dois lados da regra:

1. **Excluiria produto válido** — 165 linhas têm `last_sale` em D0, e **164
   delas têm `first_sale` anterior a 01/09**. São produtos veteranos que
   simplesmente venderam hoje; desapareceriam inteiros do ranking.
2. **Preservaria métrica já contaminada** — `gold.ml_produto_ranking` é VIEW
   sobre `gold.ml_produto_pnl`, também VIEW, que agrega
   `raw.ml_order_line_items` com **zero filtro temporal**. `first_sale` e
   `last_sale` são `min()`/`max()` de `date_created`: atributos, nunca
   dimensões. Pior, `ml_produto_pnl` calcula `sum(gross_revenue) AS
   brand_revenue` e uma window `sum(...) OVER (PARTITION BY brand ORDER BY
   gross_revenue DESC)` — uma venda de D0 em **qualquer** produto move o
   share e pode reordenar o Pareto de **todos** os produtos da marca,
   inclusive dos que não venderam em D0. Filtrar linha não desfaz isso.

D0 contribuiu com 795 pedidos e 808 unidades em 165 itens distintos.

O grão `(brand, item_id)` também não é limpo: **117 chaves duplicadas**, e o
`sync_produtos` desempata com `DISTINCT ON ... ORDER BY gross_revenue DESC`.

**Mudança mínima necessária**, em ordem de preferência:

1. **Snapshot diário imutável** da própria view no Data Mart
   (`gold.ml_produto_ranking_snapshot(ref_date, brand, item_id, …)`), gravado
   uma vez por dia com a foto de D−1. O `sync_produtos` filtraria
   `ref_date = :date_to` — aí sim um teto real, de uma linha, auditável.
2. `ml_produto_pnl`/`ml_produto_ranking` aceitarem parâmetro de corte, para
   que acumulados **e** comparativos sejam calculados até D−1.
3. Publicar só quando a fonte inteira estiver em D−1 — inútil: qualquer dia
   com venda tem `max(last_sale) = D0`.

`gold.ml_produto_diario` existe e tem grão produto × dia, mas **não tem** os
campos relativos à população (`revenue_share_pct`, `cumulative_revenue_pct`,
`pareto_bucket`) nem os derivados. Reconstruir o ranking a partir dela seria
trazer a regra de negócio do Data Mart para dentro da Torre — mudança de
fórmula e de propriedade, não teto. Enquanto nenhuma das três opções existir,
a etapa fica **BLOQUEADA**, e bloqueada é o estado correto.

## 3. Interrupção do `full_daily` — kill externo de disparo manual

Execução investigada: `logs/full_daily_20260908_092928_*`. **stdout e stderr
com 0 byte os dois.**

| Pergunta do gate | Resposta por evidência |
|---|---|
| Último step iniciado | **nenhum** — o orquestrador não imprimiu a primeira linha |
| Último step concluído | **nenhum** |
| Primeiro ponto sem conclusão | o arranque do filho, entre 09:29:29 e a primeira escrita, que nunca veio |
| Natureza | **kill externo**. `LastTaskResult` = 3221225786 = `0xC000013A` = `STATUS_CONTROL_C_EXIT` |
| Por que o lock sobreviveu | `finally` **não pode** rodar sob `TerminateProcess` — em nenhuma linguagem |
| `finally` confiável | **sim**, no que está ao seu alcance (exceção, timeout, exit não-zero) |
| Tratamento de lock órfão | **sim, e já existia** — na aquisição, não na saída |

Timeout está descartado: produziria a linha `Timeout de N s atingido` e exit
124. Falha de conexão está descartada por contraste direto — a execução de
07/09 14:36 teve 13,0K de stdout com todos os steps BLOCKED por
`RDS/Data Mart: falha de conexao`.

O lock foi criado 09:29:28 e modificado 09:29:29; esse 1s é a troca atômica
`wrapper → filho` de `Update-LockOwnerPid`, o que prova que o PID gravado era
o do processo filho.

### O padrão é recorrente, e não é da tarefa agendada

Em todo o histórico: **7 de 42 execuções (17%)** têm stdout vazio, e **6
dessas 42** têm stdout *e* stderr vazios — 16/07 10:00, 29/07 11:17, 30/07
12:15, 17/08 01:01, 23/08 08:56, 03/09 08:53, 08/09 09:29.

**Nenhuma às 06:00**, que é o horário agendado. Todas em horário comercial e
em minutos quebrados.

O que isso **prova**: a execução agendada das 06:00 **não está entre as
interrompidas**, e portanto não há evidência de que o Scheduler das 06:00
esteja defeituoso.

O que isso **não prova**: que a causa seja disparo manual interrompido por
quem o disparou. Esse é o cenário mais compatível com os horários e com o
código de saída, mas **nada nos logs registra quem iniciou cada execução** —
`run_with_lock.ps1` não grava origem do disparo, e o histórico do Task
Scheduler não foi correlacionado por tipo de gatilho. Fica como **hipótese
não confirmada**.

### Correção do próprio Gate D08-R

A remoção manual de `full_daily.lock` no D08-R era **desnecessária**. Uma das
quatro provas que levantei foi "PID ausente" — que é exatamente a condição
usada por `Test-LockOwnerAlive` para recuperar sozinho. A execução seguinte
teria removido o lock e rodado. Nada estava bloqueado de fato.

### Duas lacunas reais, deliberadamente não corrigidas aqui

1. **A recuperação decide por uma única condição: o PID está vivo?** O gate
   exige cinco cumulativas — PID ausente, identidade incompatível, idade acima
   de limite documentado, ausência de advisory lock e ausência de run ativo —
   mais operação atômica e auditável. O runner atende **uma**.
   Consequência: **reuso de PID**. Se o número de um lock órfão for reciclado
   por um processo qualquer, `Test-LockOwnerAlive` responde "vivo" e todo
   `full_daily` futuro fica BLOCKED. O sentido inverso — recuperar o lock com
   uma execução real viva — **não** é possível, porque o lock carrega o PID do
   filho vivo: a falha é de disponibilidade, nunca de concorrência sobre dado.
2. **A recuperação é silenciosa nos logs.** O `Write-Warning` da recuperação é
   emitido antes de `$stdoutLog`/`$stderrLog` existirem. Nenhuma recuperação
   passada deixou rastro em `logs/` — foi por isso que a investigação não
   achou histórico delas.

Nada disso foi implementado neste gate **de propósito**: o reuso de PID não
causou nenhum dos 7 incidentes (todos explicados por kill externo), e o gate
manda implementar só com causa comprovada. A primeira correção, quando for
priorizada, é a condição de **identidade** — comparar nome/caminho do processo
dono antes de aceitar "vivo", em vez de confiar no número.

## 4. `barbours` ausente no PMA de 07/09 — `source_missing`

Classificação: **`source_missing`**. A observação nunca foi feita.

`silver.stg_ml_item_price_history`, itens por marca e dia:

| marca | 04/09 | 05/09 | 06/09 | 07/09 | 08/09 |
|---|---|---|---|---|---|
| barbours | 313 | 313 | 313 | **AUSENTE** | 314 |
| kokeshi | 219 | 219 | 219 | 219 | 219 |
| lescent | 145 | 145 | 145 | 145 | 145 |
| rituaria | 182 | 182 | 182 | 182 | 182 |
| **total** | 859 | 859 | 859 | **546** | 860 |

546 = 219 + 145 + 182 **exatamente**: a lacuna é barbours inteira,
tudo-ou-nada. Nenhuma outra marca perdeu um único item.

- **Não é `ingestion_failure`** — não existe camada a montante para ter
  falhado. Varredura de `pg_class` no schema `raw` por `%price%`, `%item%` e
  `%listing%`: **zero relação**. A silver É a camada mais a montante desse
  dado.
- **Não é `mapping_failure`** — `silver.stg_ml_items` tem os 348 itens
  barbours, o join é total, e é o mesmo mapeamento que entregou 860 linhas em
  08/09.
- **Não é `serving_failure`** — o serving reproduziu 546 anúncios em 3 marcas,
  sem inventar linha, repetir 06/09 ou virar ausência em zero.

**Assinatura que identifica a causa:** `stg_ml_item_stock_history` perdeu o
**mesmo** dia e a **mesma** marca (3 marcas, 546 linhas), mas
`stg_ml_item_visits` teve as **4 marcas** em 07/09 (537 linhas), e
`stg_ml_ads_items` tem 1.307 linhas nesse dia. O que isso **prova**: a ausência é de origem, não de carga, mapeamento ou
serving, e não foi um apagão geral da coleta ML naquele dia.

O que isso **não prova**: que preço e estoque venham da mesma passada
particionada por marca e que essa partição tenha falhado. É o cenário mais
compatível com as três medições, mas **a causalidade externa não foi
comprovada**: o job de captura vive fora deste repositório, não há tabela de
auditoria dele no Data Mart, e nada foi lido do lado do coletor. Fica como
**hipótese não confirmada**, e o limite honesto do diagnóstico é a
classificação `source_missing`.

**É a primeira ocorrência em 80 dias.** Dias faltantes por marca em toda a
janela (20/06 a 08/09): rituaria 14 (todos antes da entrada da marca, cujo
primeiro dado real é 02/07), barbours 2 (23/07 e 07/09), kokeshi 1 (23/07),
lescent 1 (23/07). O 23/07 faltou para todas — interrupção global. Logo 07/09
é o **único** caso de falha parcial por marca.

**Irrecuperável.** Preço anunciado é observação pontual de vitrine:
reprocessar hoje leria o preço de hoje. Copiar 06/09 para 07/09 fabricaria
observação que nunca foi feita. O dia fica com 3 marcas, declarado.

## 4-bis. Por que estes três incidentes não viraram testes permanentes

Os incidentes acima (lock do `full_daily`, NO-GO do ranking ML e lacuna
`barbours`) foram levantados no DQ-D2 como três arquivos de teste. Eles foram
**removidos** no DQ-D2-R, e a evidência útil está neste documento. O motivo é
de projeto, não de arrumação:

1. `test_dqd2_full_daily_lock_lifecycle.py` **exigia que o defeito
   permanecesse**: fixava que `Test-LockOwnerAlive` não valida identidade de
   processo e que a recuperação não emite aviso auditável. Endurecer o lock —
   exatamente o que a dívida pede — quebraria a suíte. Um teste que reprova a
   correção do próprio risco que descreve está invertido.
2. `test_dqd2_ml_ranking_ceiling_nogo.py` congelava narrativa: afirmava, por
   busca textual, que não existe solução para o teto do ranking. Isso trava a
   solução futura em vez de proteger um comportamento.
3. `test_dqd2_pma_barbours_gap_20260907.py` **não provava o incidente**. As
   medições de 07/09 estão no Data Mart, não no código; o arquivo testava
   propriedades genéricas (allowlist, ausência de `generate_series`) que valem
   com ou sem o incidente.

Regra que fica: incidente e medição vão para documentação; teste permanente é
para comportamento que deve continuar valendo.

## 5. Shopee — bloqueios registrados, nada executado

1. **Ads — BLOQUEADA.** O parser não tem grão diário: agrega o total do
   período e divide por `num_days`, com as datas vindas do **cabeçalho do
   próprio CSV**, não da janela do CLI. Nenhum parâmetro de linha de comando
   corrige o denominador. **Para desbloquear: exportar Ads com `Período`
   terminando exatamente em D−1.** Enquanto isso, `ad_spend` permanece
   **NULO** — ausência preservada como ausência, nunca zero.
2. **Silver/Gold — sem executor oficial versionado.**
   `pipelines/staging/shopee/build_sql.py` só monta e escreve texto.
3. **Raw nova não alimenta a Torre automaticamente.** Não há propagação
   Raw → Silver: `shopee_batch_window.py` declara na própria linha 43 que
   nunca lê `raw.shopee_ingestion_file` — lê
   `silver.stg_shopee_order_item_snapshots`. O caminho está cortado na
   origem, não apenas com teto.
4. **Não construir o runner Silver agora**, com a estratégia de API Shopee em
   arbitragem sob o SH-API-2A. Risco registrado para quem o construir:
   `_validate_shopee_window` rejeita apenas `date_to > today`, ou seja
   **aceitaria D0** — é ali o ponto de vazamento.

## 6. Validação executada

| Item | Resultado |
|---|---|
| Suíte `pipelines/tests` | **3.235 passed, 0 failed** |
| Baseline no mesmo HEAD (`61a904e`), worktree temporário | **3.191 passed, 0 failed** |
| Delta | **+44** (26 teto D−1 + 10 máximo elegível + 8 reconciliação) |
| Focais Regional + diagnóstico | 139 passed |
| `schedule_plan` / `orchestrate` / locks / health | 292 passed |
| Pester `run_with_lock.tests.ps1` | **17 passed, 0 failed** |
| Pester `run_task.tests.ps1` | **22 passed, 0 failed** (era 17/2) |
| `compileall` dos módulos Python alterados | OK |
| `git diff --check` | limpo, **inclusive sem aviso de LF/CRLF** |
| Migrações / lockfile / dependência nova | **zero** |
| Segredo, DSN, token, IP privado, PII, caminho pessoal | nenhuma ocorrência |
| Worktrees preexistentes | 14, todas preservadas |

### Provas de mutação

**Máximo elegível.** Reintroduzir `min(max_date_source, teto)` e
`isinstance(date_to, date)` faz **5** testes reprovarem. Os casos descrevem a
fonte por lista real de dias e deixam o *fake* calcular
`max_global`/`max_eligible` como o Postgres calcularia.

**Reconciliação.** Remover o teto de uma das duas reconciliações, uma por vez:
Shopee → **4** falhas; ML → **5** falhas. Em ambos os casos
`test_dqd2r2_carga_conclui_sem_divergencia_falsa_com_D0_na_fonte` reprova, o
que reproduz o sintoma de produção (divergência falsa + rollback), não apenas
uma diferença de texto no SQL.

Os *fakes* do R2 **calculam** o GMV: guardam um conjunto `data -> valor`,
extraem a janela de cada SQL recebido e somam só o que cai dentro dela. Os
totais não são pré-igualados — se as duas janelas divergirem, os totais
divergem, como em produção. O valor de 08/09 é material de propósito
(R$ 777.777,77 no ML e R$ 999.999,99 no Shopee), muito acima da tolerância,
para que o teste não passe por coincidência numérica.

### O Pester do `run_task` estava vermelho desde `950c407` — corrigido

Provado por execução em worktree limpo em `61a904e`: **17 passed, 2 failed**,
exatamente os dois testes corrigidos aqui. Não foi classificado como
pré-existente por inferência.

O commit `950c407` ("integra serving ao full daily") adicionou a terceira
`TaskKey`, `serving_refresh`, e o teste continuou fixando duas. A segunda
falha era pior e mais sutil: a extração do corpo de `Invoke-ResolvedTask`
procurava `"\n}\n"` num arquivo em **CRLF**, então `IndexOf` devolvia −1 e
`Substring(0, -1)` estourava. Era um teste que dependia do fim de linha do
checkout, não do código sob teste.

O contrato das **três** `TaskKeys` agora está fixado, sem afrouxar para "duas
ou mais":

| TaskKey | Lock | Timeout externo | Agendada |
|---|---|---|---|
| `full_daily` | `full_daily` | 9.000 s | **sim**, 06:00 |
| `serving_refresh` | `full_daily` — **compartilhado de propósito** | 9.000 s | não |
| `shopee_manual_refresh` | `shopee_manual_refresh` — separado | 9.000 s | não |

`serving_refresh` compartilha o lock do `full_daily` porque as duas mexem nas
mesmas fontes e no mesmo destino: compartilhar torna a sobreposição impossível
por construção. `shopee_manual_refresh` tem lock próprio porque mexe em fontes
disjuntas e pode legitimamente rodar em paralelo. O não-agendamento das duas
manuais já é travado em `test_ops_schedule_plan.py`, que exige
`len(PROPOSED_SCHEDULE) == 1` com `task_key == "full_daily"`.

### Orçamentos sincronizados (só comentários)

Comentários citavam `FULL_DAILY_STEP_TIMEOUT_BUDGET_SECONDS = 6600s`
(`run_task.ps1`) e `3600s` (`schedule_plan.py`). O valor real é **7.800 s**.

| Camada | Valor |
|---|---|
| Orçamento interno do `full_daily` | 7.800 s |
| Timeout externo do lock | 9.000 s |
| `ExecutionTimeLimit` do Scheduler | 9.600 s |
| Margem interno → externo | 1.200 s = **15,38%**, estritamente acima de 15% |

Nenhuma mudança funcional no runner nem no Scheduler.

### Regressão introduzida e corrigida no próprio gate

`test_main_diagnose_flag_ainda_funciona_apos_adicionar_terceira_flag`
substituía `run_diagnose_cli` por um lambda de zero argumentos, e a função
passou a receber `date_to`. O teste agora também fixa que, sem `--date-to`, o
valor chega `None` e o teto é resolvido dentro da função.

## 7. O que segue aberto

1. **Regional** — teto, máximo elegível e reconciliação corrigidos e
   incluídos neste gate, mas **ainda não executados**; a fonte publicada
   segue em 2026-09-01. Próximo passo é `--diagnose` somente leitura, e só
   depois a carga.
2. **`execute_first_load` sem teto** — os recalcs não-incrementais leem a
   fonte inteira. Sem impacto hoje (a primeira carga já ocorreu), mas uma
   recarga de zero publicaria D0. Fora do escopo dos gates DQ-D2*.
3. **ML produtos/ranking** — BLOQUEADO até existir snapshot diário imutável ou
   view parametrizada.
4. **Shopee Ads** — depende de export terminando em D−1.
5. **Silver/Gold Shopee** — sem executor versionado, decisão pendente do
   SH-API-2A.
6. **Endurecimento do lock órfão** — condição de identidade e auditabilidade
   da recuperação, ambas especificadas acima, nenhuma implementada.
7. **07/09 sem `barbours`** — irrecuperável; monitorar reincidência de falha
   parcial por marca. A causalidade externa segue não comprovada.
8. `comparison_basis_text` renderiza sem acentuação (cosmético, pendente do
   PMA-H1).
