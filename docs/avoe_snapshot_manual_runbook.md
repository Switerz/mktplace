# Snapshots manuais da Avoe — fonte, contrato e runbook

Gate AVH-4A · corrigido no AVH-4A-R · aplicado no AVH-4B-P · corrigido no
AVH-4A-H1, AVH-4A-H1-R, AVH-4A-H1-R2, AVH-4A-H1-R3 e AVH-4A-H1-D1 · servido em
leitura no AVH-4B-S Task 1/2 · exibido no AVH-4B-S Task 2/2 · 2026-09-08

> **Estado por etapa — não usar a frase genérica "AVH-4B não iniciado".**
>
> | Etapa | Estado |
> |---|---|
> | **AVH-4B-P** — piloto: migration + primeiro snapshot | **CONCLUÍDO** |
> | migration `015` no Neon | **APLICADA** |
> | snapshot `sync_run_id = 285` (7 metas + 24 canais) | **PRESERVADO, intocado** |
> | **AVH-4B-S Task 1/2** — contrato, serviço, rota e testes | **CONCLUÍDO** |
> | **AVH-4B-S Task 2/2** — página e QA de navegador | **CONCLUÍDO** |
> | deploy do backend no Render | **PENDENTE — ação do proprietário** |
>
> A auditoria histórica do run 285 tem `rows_extracted = 31` e
> `rows_loaded = 31`, e **não será reescrita**. A população original lida
> naquele snapshot era de **4.837 linhas** (19 metas + 4.818 diárias). Sob a
> semântica adotada no AVH-4A-H1, uma execução equivalente hoje registraria
> `rows_extracted = 4.837` e `rows_loaded = 31` (§8.5).
>
> Nada foi escrito no Data Mart e nada foi escrito na Avoe. Desde o
> **AVH-4B-S Task 1/2** existe **uma** rota de leitura consumindo estas tabelas
> — `GET /api/v1/performance/avoe-snapshot`, descrita em §13. Ela é aditiva e
> read-only: nenhum contrato existente mudou, e nenhuma métrica da Avoe entra
> em `/overview`, `/canais` ou qualquer outro endpoint. Desde a **Task 2/2**
> existe também **uma** página que a consome — `/referencias-externas/avoe`,
> descrita em §14 —, em grupo de navegação próprio e sem número nenhum da Avoe
> nas telas oficiais.
>
> Três rodadas de hotfix, todas **sem tocar nos dados publicados**: o
> **AVH-4A-H1** corrigiu o caminho de commit indeterminado, a mensagem após
> commit confirmado e a semântica de `rows_extracted`; o **AVH-4A-H1-R** fechou
> os caminhos em que uma falha da própria auditoria ainda podia produzir uma
> afirmação falsa; o **AVH-4A-H1-R2** acrescentou o estado de reversão não
> confirmada, modelou cada mutação de auditoria com quatro resultados possíveis
> e proibiu qualquer exceção crua de driver de sobreviver na cadeia; o
> **AVH-4A-H1-R3** passou a classificar as mutações de auditoria pela **fase**
> (um commit tentado que levanta é indeterminado para sempre, e nenhum rollback
> posterior o rebaixa); o **AVH-4A-H1-D1** trocou a denylist do sanitizador por
> uma regra fail-closed — texto de exceção externa nunca é ecoado — e alinhou
> comentários, docstrings e runbook ao código. Ver a matriz em §8.2.1, a
> semântica das mutações em §8.2.2 e as métricas em §8.5.

---

## 1. O que é esta fonte, e o que ela não é

O Avoe Hub é o sistema da terceirizada que opera parte dos marketplaces. Dele
saem dois dados que a Torre não tem por nenhuma outra via:

| Produto | Conteúdo |
|---|---|
| **Metas mensais por marca** | valor de meta digitado pela Avoe, por competência |
| **Faturamento informado de canais adicionais** | valor digitado para SHEIN, Magalu, Kwai, Beleza na Web, RD Marketplace e Amazon |

**Não é fonte de custo.** As auditorias AVH-3A/3B provaram que o custo do Avoe é
idêntico, ao centavo, a `silver.bling_produtos.preco_custo` — 64 de 64 SKUs
pareados, erro médio 0,00%. Qualquer necessidade de custo usa o Bling, que é
nosso, tem carga diária e não depende de terceiro. E mesmo o do Bling é **custo
corrente**, nunca CMV histórico: não há vigência temporal em nenhuma das duas
pontas.

**Não é fonte de margem.** As margens gravadas em `marcas_cotacoes` só se
reproduzem em 16,8% das linhas a partir do estado atual dos dados, porque foram
calculadas com um custo diferente do vigente ou editadas à mão. A taxa-base de
marketplace que alimenta essa margem vive *hardcoded no HTML* do Avoe
(`CANAL_CONFIG`, 12% a 25% por canal), sem versionamento.

**Não desbloqueia unit economics.** Nenhum dos três pedidos originais — custo e
retorno de afiliados, share de receita por origem, margem por anúncio — é
atendido por esta fonte.

## 2. Por que snapshot manual, e não conector

Não existe credencial técnica dedicada. A conta pessoal usada nas auditorias tem
perfil administrador, alcança telas que escrevem no Bling, e **não pode virar
secret de produção**.

Enquanto isso, o caminho é: alguém exporta o snapshot com o coletor auditado do
AVH-3A, e o importador deste gate lê o **arquivo** — nunca a Avoe. O importador
não conhece usuário, senha, token nem endpoint da Avoe. Há teste que falha se
qualquer um desses termos aparecer no código.

O custo dessa ponte é humano: alguém precisa lembrar de exportar. É ponte, não
arquitetura. §9 descreve a substituição.

## 3. Proxy: o que a palavra obriga

`reported_amount` é **"Faturamento informado pela Avoe"**. Não é GMV oficial, e
a diferença não é de rótulo:

- a definição não foi confirmada pela fonte — não sabemos se é bruto, líquido,
  se inclui cancelados ou devoluções. `definition_status = 'unconfirmed'`;
- `is_proxy = TRUE` **por CHECK**, não por convenção de quem carrega;
- a allowlist de `channel` **exclui** `tiktok`, `mercado_livre` e `shopee`. Um
  canal oficial não consegue entrar na tabela nem por erro de carga, e portanto
  não há soma acidental com o GMV oficial;
- `definition_warning` é `NOT NULL` com CHECK de não-vazio, e precisa viajar
  até a API e a tela.

**É proibido somar `reported_amount` ao GMV oficial**, em qualquer superfície,
inclusive em total executivo. Não é uma recomendação de estilo: é o motivo pelo
qual a tabela é separada.

## 4. Moeda assumida

A Avoe não declara moeda em campo, rótulo ou tooltip. O contrato aceita a
inferência de forma explícita e separa valor de estado epistemológico:

```
currency_code    = 'BRL'
currency_status  = 'assumed_unconfirmed'
currency_warning = 'Moeda nao declarada pela fonte; BRL inferido pelo contrato...'
```

Há CHECK que impede `assumed_unconfirmed` com warning vazio. A UI é obrigada a
mostrar que a moeda foi inferida. **BRL nunca é apresentada como confirmada.**

## 5. Meta e realizado nunca se misturam

As duas tabelas **não têm coluna de realizado**, e a ausência é estrutural.

A origem `resumo_marca_mes` traz um campo `faturamento` — o realizado que a Avoe
digita. Ele é lido para ser **explicitamente descartado**: não existe coluna que
o receba, e há teste que falha se `faturamento`, `realizado`, `gmv` ou
`official_realized_amount` aparecerem nas tuplas de coluna do importador.

O realizado oficial vive em `marts.fact_marketplace_daily_performance` e é
consultado de lá **no momento da leitura**, nunca copiado. Isso foi validado no
AVH-3C: os três endpoints (`/canais`, `/overview`, `/trend`) reconciliam com
diferença 0,00 para agosto/2026.

## 6. Limitações de cobertura, medidas

**Metas: vigência a partir de 2026-08-01.** Junho e julho são rejeitados por
escala incompatível — Ápice sai de R$ 100 mil em julho para R$ 5 milhões em
agosto; Barbours de R$ 1,03 mi para R$ 10 mi. Com o realizado oficial, jun/jul
produziriam atingimento de 272% a 3.237%. O CHECK é estreito de propósito:
admitir competência anterior exige migration, não flag.

**Duas marcas com meta e sem realizado.** Denavita e GoCase têm meta no Avoe e
não existem na fato publicada — o mapa `BRAND_TO_LOJA` dos transforms cobre
cinco marcas. As metas delas são preservadas; o atingimento é **não calculável**,
nunca zero.

**Uma marca com cobertura oficial parcial.** Ápice não tem Mercado Livre na fato.
O realizado dela é **piso**, e precisa ser exibido como tal.

**Canais adicionais: cobertura parcial é a regra.** No snapshot de referência, 22
das 24 competências são `partial_month`. `days_covered`, `first_business_date` e
`last_business_date` existem para que a agregação diária→mensal seja auditável.

**Corte de regime.** Só linhas com `data_referencia >= 2026-06-10` entram: antes
disso o campo da origem é uma soma móvel de 28 dias e **não é aditivo** —
somá-lo produziria R$ 1,99 bilhão onde há R$ 65 milhões.

**Ausência nunca é zero.** `reported_amount` é anulável: NULL é indisponível,
0 é zero informado. Marca ou canal que a Avoe não reportou **não gera linha**.

## 7. Versionamento: qual snapshot é o corrente

As duas tabelas são **append-only**. A PK inclui `captured_at`, então um snapshot
novo coexiste com os anteriores em vez de sobrescrevê-los. Não há UPDATE nem
DELETE em nenhum caminho do importador.

### 7.1 A unidade atômica é a CAPTURA, não a marca nem o canal

`select_current_version` escolhe o **maior `captured_at` por `(source,
ref_month)`** e devolve **exclusivamente** as linhas daquela captura.

A consequência importa: se a captura anterior tinha as marcas A e B, e a captura
nova tem só A, o resultado corrente tem **só A**. **B não é ressuscitada.** A
ausência na captura corrente é informação — a Avoe deixou de reportar aquela
marca —, não uma lacuna a preencher com o valor antigo. O mesmo vale para canal.

Isso é diferente de escolher o maior `captured_at` por marca, que produziria um
forward-fill silencioso e misturaria duas capturas numa resposta só.

Demais regras:

- **nunca** decide por `imported_at` — há teste que audita o corpo executável da
  função por AST e falha se `imported_at` aparecer nele;
- competências distintas podem ter correntes capturadas em momentos diferentes;
- versões anteriores permanecem consultáveis;
- `snapshot_id` divergente sob o mesmo `captured_at` **falha explicitamente**:
  são arquivos diferentes carimbados com a mesma captura, o que é conflito, não
  repetição.

## 8. Runbook

### 8.1 Dry-run (padrão, não escreve)

```
cd <raiz do repo>
python -m pipelines.avoe.snapshot_import --snapshot-dir <DIR_DO_SNAPSHOT>
```

O relatório mostra arquivos e hashes, `captured_at`, competências, marcas,
canais, contagens, totais por dataset, rejeições com motivo, a versão corrente
segundo a regra da §7, e as ações que *seriam* executadas. A última linha diz
`ESCRITA: nenhuma (sem --apply)`.

Antes de qualquer coisa o importador valida, e **toda falha é fail-closed**:

| Verificação | Comportamento em falha |
|---|---|
| `MANIFEST.json` presente e legível | falha |
| **`MANIFEST.sha256` OBRIGATÓRIO** — ausente, vazio, malformado, com mais de uma entrada, ou divergente | falha |
| `source_system = avoe_hub` e `status = OK` | falha |
| `captured_at` presente e **com timezone** (não se assume UTC) | falha |
| SHA-256 de cada arquivo da allowlist | falha |
| tabela declarada duas vezes no manifesto | falha |
| **`row_count` obrigatório, inteiro, ≥ 0**, comparado com a contagem física — **inclusive quando declarado zero** | falha |
| coluna **obrigatória** ausente em qualquer linha | falha |
| coluna **proibida** presente (`gmv`, `realizado`, `cpf`, …) | falha |
| coluna fora do schema declarado (obrigatória + opcional) | **falha** — campo novo exige revisão |
| **duplicata na chave de origem**, idêntica **ou** conflitante | falha |
| **canal não oficial fora da allowlist** | **falha — bloqueia o snapshot inteiro** |
| timestamp de proveniência inválido ou sem timezone | falha |

### 8.1.1 Chave de origem e duplicatas

A chave esperada é `(mes_referencia, marca)` nas metas e
`(data_referencia, marca, plataforma)` no diário. Verificada no snapshot de
referência: **4.818 linhas, 4.818 chaves, zero duplicata**.

Duplicata **falha**, seja idêntica ou conflitante. A regra é deliberadamente
conservadora: a origem não documenta duplicatas, somar duas linhas da mesma
chave inventaria um valor, e deduplicar implicitamente escolheria um lado sem
regra. A verificação acontece **antes** de qualquer agregação diária→mensal.

### 8.1.2 Canal desconhecido

Canal **oficial** conhecido (`TIKTOK`, `MELI`, `SHOPEE`) é o único descarte
silencioso, e é contabilizado no relatório — descartar oficial é o propósito da
tabela de proxy.

Canal **não oficial** fora da allowlist — um `TEMU` que apareça amanhã —
**bloqueia o snapshot inteiro**: exit 2, zero INSERT, zero commit, mesmo que o
resto do arquivo esteja perfeito. Registrar uma rejeição e continuar deixaria
faturamento de um canal novo fora da conta sem ninguém decidir isso.

### 8.1.3 Timestamps de proveniência

`source_recorded_at` usa **`atualizado_em` válido**, com **fallback para
`criado_em` válido**, e `NULL` somente quando a origem não carimbou nenhum dos
dois. Timestamp inválido **falha** — nunca vira `NULL` silenciosamente.
Timestamp sem timezone **falha** — o contrato não assume UTC. Timestamp com
offset é normalizado para UTC.

### 8.2 Apply (escreve)

```
export DATABASE_URL=<Neon>   # única credencial lida, e só aqui
python -m pipelines.avoe.snapshot_import --snapshot-dir <DIR> --apply
```

Uma transação **de dados**, `pg_advisory_xact_lock(913120041)` — chave própria,
distinta da do PMA —, `statement_timeout` de 120s e `lock_timeout` de 30s.
**Zero retry automático:** falha não é repetida; o operador lê o erro sanitizado
e decide.

### 8.2.1 Semântica real da auditoria

`audit.source_sync_run` é escrito numa **conexão independente, com commit
próprio** (`source_name = 'avoe_manual_snapshot'`): `running` **antes** da
transação de dados, e o fechamento **depois**. Isso significa que **uma
tentativa revertida deixa rastro** — o desenho anterior, de transação única,
perdia o registro no rollback e só conseguia gravar sucessos. Verificado no
PostgreSQL descartável: a variante de conteúdo divergente produziu
`sync_run_id = 3` com `status = failed` e mensagem sanitizada.

Garantias adicionais: o `UPDATE` da auditoria exige `rowcount == 1`; `status` é
validado contra `{running, success, failed}`; a mensagem passa pelo sanitizador
que suprime qualquer texto contendo DSN, senha, apikey ou JWT.

#### Matriz de desfechos (AVH-4A-H1, fechada no AVH-4A-H1-R)

O commit dos dados fica **fora** do bloco que faz `rollback()`, de propósito:
uma exceção no próprio `commit` não prova que o banco deixou de gravar, e um
rollback depois dela não desfaz um commit que pode ter sido aplicado.

O estado dos **dados** tem três valores (`nao_confirmada`, `confirmada`,
`indeterminada`), mas a **auditoria** é um eixo independente que pode falhar em
quatro momentos distintos. O cruzamento dá sete desfechos, cada um com seu exit
code e sua mensagem:

| # | Estado | Dados | Auditoria | Exceção | Saída |
|---|---|---|---|---|---|
| 1 | `audit_start` falhou | **não tentados** — `publish()` nem foi chamado | não iniciada; se a mutação foi revertida, a linha não existe; se ficou indeterminada, nada se afirma | `AuditoriaInicialIncompleta` | 7 |
| 2 | Falha pré-commit | rollback **confirmado**, nada publicado | `failed`, `rows_loaded = 0` | `PublicacaoNaoConfirmada` | 4 |
| 3 | Falha pré-commit **+** `audit_finish(failed)` falhou | rollback confirmado, nada publicado | incompleta | `AuditoriaIncompletaSemPublicacao` | 8 |
| 4 | Falha pré-commit **+** o próprio `rollback()` levantou | commit **nunca tentado**; fim da transação **não observado**; conexão encerrada | só a nota de indeterminação; **nunca** `failed` | `ReversaoNaoConfirmada` | 11 |
| 5 | Exceção **no** commit | desconhecidos, **nenhum rollback** | `running` + nota `INDETERMINADO:` | `PublicacaoIndeterminada` | 5 |
| 6 | Exceção no commit **+** a marcação falhou | desconhecidos, nenhum rollback | desconhecida; a nota pode não existir | `PublicacaoIndeterminadaAuditoriaNaoConfirmada` | 9 |
| 7 | Commit confirmado | publicados (ou no-op) | `success` | — | 0 |
| 8 | Commit confirmado **+** `audit_finish(success)` falhou | publicados (ou no-op), nenhum rollback posterior | incompleta | `AuditoriaIncompleta` | 6 |

Os estados 1, 3, 6 e 8 se desdobram conforme o resultado da mutação de
auditoria (§8.2.2): a mensagem muda, o exit code não.

**Zero retry automático** em todos. Qualquer coisa fora dessa tabela sai com
**10** e a mensagem `ESTADO NAO CLASSIFICADO` — que não afirma publicação, nem
reversão, nem auditoria.

`KeyboardInterrupt` e `SystemExit` **não** entram nessa máquina: sobem crus.
Interrupção do operador não é desfecho operacional da publicação, e convertê-la
em um deles seria inventar um estado.

A CLI tem **exatamente nove resultados**: um sucesso, sete falhas tipadas e um
fallback não classificado.

```
sucesso
0   sucesso, no-op ou dry-run

falhas tipadas da maquina de estados
4   FALHA NA PUBLICACAO (nada gravado)
5   PUBLICACAO INDETERMINADA
6   AUDITORIA INCOMPLETA (publicacao confirmada)
7   AUDITORIA INICIAL INCOMPLETA (publicacao NAO tentada)
8   PUBLICACAO NAO CONFIRMADA, AUDITORIA INCOMPLETA (nada publicado)
9   PUBLICACAO INDETERMINADA (auditoria tambem NAO confirmada)
11  REVERSAO NAO CONFIRMADA (commit nunca tentado; fim da transacao nao observado)

fallback
10  ESTADO NAO CLASSIFICADO (nao se afirma publicacao, reversao nem auditoria)
```

**Os exits 2 e 3 ficam fora da máquina de estados.** O `2` é falha de contrato
do snapshot e o `3` é falha de credencial ou de conexão: ambos acontecem antes
de qualquer transação existir, então não há publicação, reversão nem auditoria
sobre a qual afirmar coisa alguma.

Nenhum dos rótulos contém "rollback" ou "revertido".

#### Estado 4 — reversão não confirmada

É o único caminho em que o processo não sabe se a transação de dados terminou.
O que ele **sabe** é que o `commit()` nunca foi tentado. O que ele **não pode
dizer**: "rollback aplicado", "dados seguros", nem "nada gravado" sem
qualificação.

O que o importador faz: **tenta** encerrar a conexão para forçar o fim da
transação e registra o resultado dessa tentativa em `conexao_encerrada` —
`True` se o `close()` retornou, `False` se ele também levantou. A mensagem
acompanha o atributo; nunca se afirma incondicionalmente que a conexão foi
encerrada. Na auditoria grava apenas a nota de indeterminação — nunca `failed`,
que afirmaria um fim que ninguém observou — e sai com 11 mandando reconciliar
em leitura. Nenhum retry.

A falha do `close()` **não substitui nem esconde a classificação principal**: o
desfecho continua `ReversaoNaoConfirmada` com exit 11, e a causa original segue
identificada.

Quatro afirmações que o importador **não** faz:

- **nunca reproduz o texto de uma exceção externa.** A regra é **fail-closed**:
  de uma exceção de driver ou biblioteca sobra apenas o nome da classe, passado
  por allowlist de caracteres, e a mensagem fixa `<mensagem externa
  suprimida>`. Só o texto de `SnapshotImportError` — construído dentro do
  próprio importador — é preservado. Isso vale para a mensagem *aparentemente*
  inofensiva também: julgar isso em tempo de execução não é auditável, e a
  denylist anterior (`postgres://`, `password`, `apikey`, `eyJ`) deixava passar
  DSN em outra caixa, `host=`/`user=`/`dbname=` em key-value, caminho de
  arquivo, SQL com parâmetros e `DETAIL` de constraint com valor de linha.
  Todo desfecho sai por `_levanta()`, que zera `__cause__`, `__context__` e o
  traceback de origem, então nada disso chega a stderr, a
  `audit.source_sync_run` ou a `traceback.format_exception`. O importador
  também não emite log — não há logger para vazar;

- **"rollback aplicado" só aparece quando o `rollback()` retornou.** A frase é
  construída dentro de `publish()`, no ramo que a comprova. Se o próprio
  rollback levantar, a mensagem passa a dizer que a reversão **não** foi
  confirmada — e o desfecho passa a ser o estado 4. Nenhum rótulo da CLI contém
  essa frase, em nenhuma das sete falhas tipadas;
- **nunca marca `failed` num run cujo commit retornou**, nem num run cujo
  commit ficou indeterminado, nem num run cuja reversão não foi confirmada. Um
  registro que ficou `running` é a declaração honesta de "não sei";
- **nunca chama de "commit indeterminado" uma falha pré-commit.** O estado 3 é
  explícito: a transação de dados foi revertida com confirmação, e o que ficou
  aberto é a auditoria.

Se você encontrar um run `running` desta fonte, o procedimento é: contar as
linhas das duas tabelas para aquela `captured_at` e comparar com o relatório do
dry-run. Se as linhas estiverem lá, a publicação aconteceu e o que falta é
apenas fechar o registro de auditoria; se não estiverem, a captura pode ser
reimportada normalmente (o caminho de idempotência cobre os dois casos sem
sobrescrever nada). Nos estados 3, 4 e 6 vale a mesma leitura, com uma
ressalva: ali o próprio registro de auditoria pode estar sem nota, então o
único árbitro é a contagem nas tabelas de snapshot.

### 8.2.2 As quatro faces de cada mutação de auditoria

Uma exceção vinda de `audit_start`, `audit_finish` ou `audit_mark_indeterminate`
**não diz, sozinha, o que ficou persistido**. O que separa os casos é o que
aconteceu depois da falha — e é isso que `ResultadoAuditoria` registra:

| Resultado | Quando | O que se pode afirmar da linha |
|---|---|---|
| `nao_tentada` | a mutação nem chegou a ser emitida | nada mudou |
| `confirmada` | o `commit()` da auditoria **retornou** | o novo conteúdo está lá |
| `revertida` | a mutação falhou **antes do commit** e o `rollback()` da auditoria **retornou** | o conteúdo **anterior** permanece |
| `indeterminada` | (a) falhou antes do commit **e** o rollback também não retornou; ou (b) o **commit foi tentado e levantou** | **nada** — o conteúdo não é observável |

**A classificação vem da fase, não do rollback.** Se o `commit()` da auditoria
foi tentado e levantou, o resultado é `indeterminada` **para sempre**: nenhum
rollback é tentado depois, e um rollback que retornasse não rebaixaria o
resultado para `revertida`. É a mesma regra da transação de dados — um rollback
não desfaz um commit que pode ter sido aplicado, e o fato de ele retornar não
prova nada sobre a linha. O caso real que isso cobre: o servidor aplica o
commit e a **confirmação se perde no caminho de volta**.

Toda exceção da máquina carrega um `resultado_auditoria`. O padrão é
`nao_tentada`, verdadeiro para o que `publish()` levanta sozinho, antes de
qualquer interação com a auditoria.

Consequências diretas nas mensagens:

- `audit_start` indeterminado: a publicação não começa, e o texto diz que não se
  afirma se a linha de run existe nem com que status — em vez de garantir que
  ela não foi criada;
- `audit_finish` revertido: aí sim dá para dizer que o run permanece `running`,
  porque a reversão foi observada;
- `audit_finish` indeterminado: o texto **não** diz "ficou running"; diz que o
  estado do registro não pode ser afirmado;
- `audit_mark_indeterminate` indeterminado: não se afirma nem que a nota existe,
  nem que não existe.

Qualquer resultado incerto exige **reconciliação em leitura** antes de nova
execução, e a mensagem diz isso explicitamente.

Isto **não** é uma transação distribuída. Dados e auditoria são dois recursos
independentes, com conexões e commits próprios — numa execução bem-sucedida são
1 commit de dados e 2 de auditoria (o `start` e o `finish`). O processo apenas
se recusa a afirmar sobre um recurso o que só observou no outro.

As colunas `marketplace_id` e `loja_id` ficam NULL de propósito: os canais
adicionais não existem em `marts.dim_marketplace` e duas das marcas não existem
em `marts.dim_loja`.

### 8.3 Idempotência independente do run ID

A equivalência entre o gravado e o lido compara **somente as colunas de negócio
e proveniência**. `import_run_id` e `imported_at` são operacionais da execução e
ficam **fora** da comparação.

- mesma captura, mesmo conteúdo de negócio, **run id diferente** → **no-op**,
  `rows_loaded = 0`, run marcado `success` com nota. Reexecutar não exige
  reutilizar o run id anterior;
- mesma captura, conteúdo de negócio divergente → **recusa**, com rollback.
  Tabela append-only: nada é sobrescrito. Reimportar exige captura nova;
- `snapshot_id` faz parte da comparação, porque é proveniência: arquivos
  diferentes sob a mesma `captured_at` são conflito.

### 8.4 Rollback e recuperação

Qualquer exceção **anterior ao commit** dispara `rollback()` da transação
inteira — inclusive falha de import do driver, que é feito **dentro** do `try`
justamente por isso. Não há estado parcial possível: ou as duas tabelas recebem
a captura, ou nenhuma recebe.

A partir do `commit`, rollback deixa de existir como recurso. Exceção no próprio
`commit` é **indeterminada** e exceção depois dele é **auditoria incompleta**;
em nenhum dos dois o importador tenta reverter, porque não há o que reverter com
segurança. Ver a matriz de desfechos em §8.2.1.

O `rollback()` em si é chamado dentro de um guard: se ele levantar, o desfecho
deixa de ser `PublicacaoNaoConfirmada` e passa a ser `ReversaoNaoConfirmada`
(estado 4, exit 11) — classe própria, **não** subclasse da outra, justamente
para que nenhum consumidor herde a garantia de "nada publicado". A frase
"rollback aplicado" existe em exatamente um ponto do código, no ramo em que o
`rollback()` retornou.

Se for necessário desfazer uma captura já comitada, **não há caminho pelo
importador** (não existe DELETE). A remoção é operação manual deliberada, fora
deste runbook, e exige autorização própria — a imutabilidade é a proteção, não
um obstáculo a contornar.

Reverter o schema: `alembic downgrade 014` remove apenas os dois objetos criados
pela `015`. Nenhum objeto oficial é tocado.

### 8.5 `rows_extracted` e `rows_loaded` (corrigido no AVH-4A-H1)

Os dois campos de `audit.source_sync_run` medem coisas diferentes, e usar o
mesmo número nos dois escondia justamente o que a auditoria deveria mostrar: a
razão de agregação.

| Campo | O que é | Sob a semântica corrigida |
|---|---|---|
| `rows_extracted` | linhas **lidas** dos arquivos do snapshot | **4.837** |
| `rows_loaded` | linhas **gravadas** nas tabelas de destino | **31** |

A queda de 4.818 para 24 não é perda: as diárias de canal são agregadas para o
grão mensal por marca × canal, e as metas fora da competência corrente são
descartadas pela regra de versão. Com os dois campos iguais a 31, essa
transformação ficava invisível na auditoria.

#### Os três números, sem ambiguidade

1. **Registro histórico real do snapshot 285** (o que está gravado hoje em
   `audit.source_sync_run`):
   `rows_extracted = 31`, `rows_loaded = 31`.
2. **População original lida pelo importador** naquele snapshot:
   19 linhas de metas + 4.818 linhas de canais = **4.837 linhas**.
3. **Execução equivalente futura**, pela semântica corrigida no AVH-4A-H1:
   `rows_extracted = 4.837`, `rows_loaded = 31`.

O snapshot 285 e sua auditoria histórica **não serão reescritos**. O `31` do
item 1 permanece: `audit.source_sync_run` é o rastro do que aquela execução
declarou, e alterá-lo seria falsear auditoria. Os dados publicados continuam
corretos — as 7 metas e os 24 canais são exatamente os esperados, e nada nas
tabelas de snapshot depende desses contadores.

## 9. Substituição futura pela credencial técnica

O snapshot manual é ponte. O destino é um papel dedicado na Avoe:

```
Papel Postgres read-only, ex.: avoe_torre_ro
  GRANT SELECT nas seis tabelas aprovadas, e só nelas
  SEM INSERT / UPDATE / DELETE / TRUNCATE
  SEM EXECUTE em função ou RPC
  SEM acesso a auth.*, storage.*, wms_*, suporte_*, log_auditoria
  RLS habilitada por tenant
  chave própria e rotacionável, distinta da anon pública
  rate limit compatível com paginação de 1.000 linhas via Range
```

Quando existir, o conector GET-only substitui a etapa de exportação humana. O
contrato de dados desta fundação **não muda**: as mesmas tabelas, os mesmos
CHECKs, a mesma regra de `captured_at`. Muda apenas quem produz o snapshot.

Bloqueios externos que permanecem:

1. credencial técnica dedicada — bloqueia produção;
2. definição formal de `reported_amount` pela Avoe — mantém `unconfirmed`;
3. confirmação de moeda — mantém `assumed_unconfirmed`;
4. catálogo Shopee de Kokeshi e Lescent na Torre — bloqueia o produto de
   estoque/FULL, que **não** faz parte deste gate.

## 10. Objetos criados por esta fundação

| Objeto | Grão | Chave |
|---|---|---|
| `marts.proxy_avoe_brand_monthly_target_snapshot` | competência × marca × captura | `(source, captured_at, ref_month, brand)` |
| `marts.proxy_avoe_extra_channel_monthly_snapshot` | competência × marca × canal × captura | `(source, captured_at, ref_month, brand, channel)` |

Nenhuma view, nenhum total consolidado materializado, nenhuma alteração em
objeto oficial de marketplace.

## 11. Validação executada no PostgreSQL descartável

Cluster **PostgreSQL 16.14** criado com `initdb` fora do repositório, em porta
própria, autenticação `trust` local e sem senha; destruído no fim da rodada.
Nada do cluster local existente do projeto foi tocado, e nem Neon nem Data Mart
foram acessados.

| # | Passo | Resultado |
|---|---|---|
| 1 | cadeia Alembic `001 → 015` | aplicou sem erro |
| 2 | inspeção real das duas tabelas | 15 e 23 colunas, tipos e nullability conforme o DDL; 13 e 20 CHECKs; 3 índices próprios cada; comentários presentes |
| 3 | `downgrade 015` | removeu **exatamente 10** objetos (2 tabelas + 2 PKs + 6 índices) |
| 4 | fato oficial após downgrade | `fact_marketplace_daily_performance` **intacta** |
| 5 | `upgrade head` novamente | voltou ao mesmo estado |
| 6 | primeiro `--apply` (run id A) | **7 metas + 24 canais** |
| 7 | reconciliação | 7 e 24 no destino, 1 `import_run_id` cada, `source_recorded_at` preenchido em 100% |
| 8 | segundo `--apply` (run id **B**, diferente) | **no-op**, 0 inseridas, tabelas seguem 7 e 24 |
| 9 | mesma `captured_at` com conteúdo alterado | **recusado**, exit 4, rollback, auditoria `failed` |
| 10 | duplicata na chave diária | **recusado**, exit 2, zero INSERT |
| 11 | canal desconhecido (`TEMU` sintético) junto de dados válidos | **snapshot inteiro recusado**, exit 2, zero INSERT |
| 12 | auditoria | 3 registros: `success` (31 linhas), `success` (no-op, 0), `failed` (0, mensagem sanitizada) |

O que **não** foi validado em banco real: todos os desfechos de falha exigem que
o `commit`, o `rollback`, o `close` ou uma mutação de auditoria levante, e nada
disso é reproduzível sem injetar defeito no driver. A cobertura é por conexão
falsa percorrendo o fluxo real de `apply_with_audit()`, com **spies diretos**
contando cada chamada de `publish`, `commit`, `rollback`, `audit_start`,
`audit_finish` e `audit_mark_indeterminate`. As provas em AST ficaram como
complemento, não como prova principal.

## 12. Validação executada nos hotfixes (H1, H1-R, H1-R2)

Rodadas **sem banco**: nenhuma conexão foi aberta, nenhum `--apply` foi
executado, e o snapshot já publicado não foi alterado nem reprocessado.

| # | Passo | Resultado |
|---|---|---|
| 1 | `pytest pipelines/tests/test_avoe_snapshot_import.py` | **169 passaram** (96 antes do H1: +12 no H1, +17 no H1-R, +24 no H1-R2, +9 no H1-R3, +11 no H1-D1) |
| 2 | `pytest pipelines/tests` na árvore modificada | 1 falha, o resto passando |
| 3 | `pytest pipelines/tests` na árvore limpa de `origin/main`, mesmo ambiente | **a mesma 1 falha** |
| 4 | comparação por node ID (limpa × modificada) | **zero removido**; só o arquivo Avoe cresce |
| 5 | `compileall` de `pipelines/avoe` e do arquivo de testes | sem erro |

A falha é `test_sync_tiktok_serving.py::test_j09_fracionarios_pequenos_nao_perdem_precisao`,
com node ID e assertion idênticos nas duas árvores
(`assert sum(0.1 for _ in range(10)) != 1.0` → `assert 1.0 != 1.0`, linha 1114):
uma hipótese de precisão de ponto flutuante que não se sustenta neste
interpretador. É **pré-existente e alheia** a esta frente, pelo critério
completo — mesmo node ID, mesmo traceback, nenhuma falha nova, nenhuma falha
desaparecida.

Contraprovas comportamentais: cada linha da matriz de §8.2.1 é exercitada de
ponta a ponta com falha injetada, conferindo exit code, contagem exata de
chamadas, status escritos na auditoria e o `ResultadoAuditoria` de cada mutação.
Somam-se a elas as contraprovas de vazamento (`str`, `repr`,
`traceback.format_exception`, stderr real da CLI, logger, `__cause__` e
`__context__`) e as de propagação de `KeyboardInterrupt` e `SystemExit`.


## 13. Serving read-only — AVH-4B-S Task 1/2

**Estado: CONCLUÍDO.** Contrato, serviço, rota e testes existem. **Task 2/2
(tela e QA de navegador) não iniciada.** Nenhuma migration acompanha esta
etapa, nenhum snapshot foi executado e nada foi escrito no banco.

### 13.1 Rota

```
GET /api/v1/performance/avoe-snapshot
```

Sem parâmetro, de propósito: o contrato é "a última captura **válida**". Filtro
por marca, canal ou competência é escopo de tela, não de serving. Segue a
convenção kebab-case das rotas existentes sob `/api/v1/performance`.

Arquivos: [apps/api/app/schemas/avoe_snapshot.py](../apps/api/app/schemas/avoe_snapshot.py),
[apps/api/app/services/avoe_snapshot_service.py](../apps/api/app/services/avoe_snapshot_service.py),
rota adicionada em [apps/api/app/routers/performance.py](../apps/api/app/routers/performance.py).

### 13.2 Fonte e grão

| Bloco | Tabela | Grão | Chave |
|---|---|---|---|
| `targets` | `marts.proxy_avoe_brand_monthly_target_snapshot` | competência × marca × captura | `(source, captured_at, ref_month, brand)` |
| `extra_channels` | `marts.proxy_avoe_extra_channel_monthly_snapshot` | competência × marca × canal × captura | `(source, captured_at, ref_month, brand, channel)` |

Os dois blocos são lidos com o **mesmo** `source` e o **mesmo** `captured_at`.
Uma captura só é servida se as duas tabelas concordarem no `captured_at`, no
`snapshot_id` e no `import_run_id` — zero mistura entre capturas.

### 13.3 Envelope da resposta

Quatro blocos: `meta`, `targets`, `extra_channels`, `limitations`.

`meta` traz `status` (`available`/`unavailable`), `source` (`avoe_hub`),
`source_kind` (`external_manual_snapshot`), `is_official_torre_source` (fixo em
`false`), `captured_at`, `snapshot_id`, `sync_run_id`, `sync_run_status`,
`sync_run_link_method`, `targets_count`, `channel_rows_count`,
`target_ref_months`, `channel_ref_months`, `currency`, `currency_status`,
`refreshed_at` (instante desta resposta, distinto de `captured_at`),
`captured_age_days`, `unavailable_reason` e `warnings`.

Não existe campo de realizado, atingimento, margem, variação ou GMV. Cruzar
meta com realizado exige contrato próprio e uma definição aprovada de
realizado; nenhuma das duas existe.

`import_run_id`, `source_file` e `source_file_hash` **não** são expostos: são
operacionais e de sistema de arquivos. `snapshot_id` é exposto porque é
proveniência — hash de conteúdo dos arquivos da captura.

### 13.4 Seleção da captura

Candidatas ordenadas da mais nova para a mais antiga (teto de 10). A primeira
que passa em **todas** as validações é servida; uma captura mais nova mas
inválida é **ignorada** em favor da anterior que se sustenta.

Motivos de recusa, todos fail-closed:

| `unavailable_reason` | Quando |
|---|---|
| `no_snapshot_published` | nenhuma captura nas tabelas |
| `targets_and_channels_capture_mismatch` | a captura existe em só uma das duas tabelas |
| `capture_incomplete` | candidata sem `captured_at` |
| `capture_mixes_multiple_imports` | mais de um `snapshot_id`/`import_run_id` sob a mesma captura, ou os dois blocos com `snapshot_id` diferente |
| `duplicate_grain_in_capture` | contagem de linhas diverge das combinações distintas da chave |
| `audit_run_not_conclusive` | nenhum ou mais de um run `success` associável |
| `serving_inconsistent` | moeda não única na captura |

Sem captura válida: **HTTP 200**, `status = "unavailable"`, arrays vazios,
`unavailable_reason` factual. Nenhuma outra rota é afetada.

### 13.5 A ligação com a auditoria é temporal — limitação medida

`audit.source_sync_run` **não tem coluna de ligação** com as tabelas de
snapshot: não existe `captured_at`, `snapshot_id` nem `import_run_id` lá.
Verificado no Neon em 2026-09-08.

A única associação possível sem migration é a janela
`[started_at, finished_at]` do run contra o `imported_at` das linhas. O serviço
é fail-closed: aceita a captura apenas quando **exatamente um** run `success`
cobre a janela. Zero runs — o caso de um run que ficou `running`, com
`finished_at` nulo — ou mais de um derrubam para `audit_run_not_conclusive`.
Assim um snapshot cujo run está `running`, `failed` ou indeterminado nunca é
servido.

O método viaja na resposta, em `meta.sync_run_link_method = "audit_time_window"`,
e num aviso. **Recomendação para a Task 2/2 ou para uma rodada de migration
própria:** acrescentar `captured_at` (ou `snapshot_id`) a
`audit.source_sync_run`, ou uma tabela de ligação, para que a associação passe
a ser estrutural.

### 13.6 Consultas e cardinalidade

No máximo **quatro** consultas, todas parametrizadas, número independente do
volume — zero N+1:

1. candidatas a captura com os agregados de validação das duas tabelas;
2. runs de auditoria desta fonte (teto de 200, mais novo primeiro);
3. metas da captura escolhida;
4. canais da captura escolhida.

Sem captura publicada, roda **uma** consulta e as duas últimas são puladas.

Cardinalidade medida no Neon em 2026-09-08: 7 linhas de meta (todas em
`2026-08-01`) e 24 de canal (6 canais × 3 competências: `2026-06-01`,
`2026-07-01`, `2026-08-01`), zero duplicidade de chave em ambas.

### 13.7 Ausência nunca é zero

`reported_amount` é `Optional[float]`: `null` significa "a Avoe não informou" e
**jamais** é substituído por `0.0`. Zero informado continua chegando como
`0.0`. Os dois estados são distinguíveis no JSON. O mesmo vale para
`brand_key`, `currency_warning` e `source_recorded_at`.

### 13.8 Limitações declaradas no próprio contrato

O bloco `limitations` afirma, em campos booleanos: `manual_snapshot: true`,
`automated_refresh: false`, `channel_amount_definition_confirmed: false`,
`currency_confirmed: false`, `provides_realized_amount: false`,
`provides_attainment_or_margin: false`, `replaces_canonical_torre_kpi: false`.

Os avisos em `meta.warnings` repetem isso em texto para o operador: fonte
externa e manual, moeda assumida, canal proxy com definição não confirmada,
ausência de realizado, ausência de automação e o método temporal do
`sync_run_id`.

### 13.9 Separação da Torre canônica

- o serviço não consulta `gold.`, `raw.` nem `silver.` — o backend no Render
  não alcança o Data Mart;
- não consulta `fact_marketplace_daily_performance`, `dim_loja`,
  `dim_marketplace` nem qualquer objeto canônico;
- a allowlist da tabela de canais **exclui** TikTok, Mercado Livre e Shopee por
  CHECK, o que impede soma acidental com o GMV oficial;
- teste dedicado varre o OpenAPI e falha se qualquer schema que não seja da
  Avoe passar a mencionar `avoe`, `proxy_avoe`, `reported_amount` ou
  `target_amount`.

### 13.10 Validação executada

| # | Passo | Resultado |
|---|---|---|
| 1 | `pytest apps/api/tests/test_avoe_snapshot_contract.py` | **46 passaram** |
| 2 | `pytest apps/api/tests` (com a mudança) | 43 falhas, 1.052 passaram |
| 3 | `pytest apps/api/tests` na árvore limpa de `origin/main` | **as mesmas 43 falhas**, 1.006 passaram |
| 4 | node ID (limpa × modificada) | 1.057 → 1.103; **zero removido**, 46 adicionados, todos no arquivo Avoe |
| 5 | reconciliação real read-only contra o Neon | **APROVADA** (§13.11) |

As 43 falhas são pré-existentes em `origin/main` e alheias a esta frente:
dependem de banco local e de Data Mart, indisponíveis no ambiente da rodada.
Zero falha nova, zero falha desaparecida.

### 13.11 Reconciliação read-only com o snapshot 285

Sessão `SET TRANSACTION READ ONLY`, serviço executado contra o Neon de verdade
e comparado com `SELECT` diretos:

- `status = available`, `captured_at` do endpoint igual ao `max(captured_at)` de
  **ambas** as tabelas;
- **7 metas** e **24 canais**, iguais às contagens da tabela para aquela
  captura;
- soma das metas e soma informada dos canais idênticas **ao centavo**;
- comparação **linha a linha por chave**: mesmas chaves e mesmos valores ao
  centavo nos dois blocos, zero divergência;
- `snapshot_id` do endpoint igual ao da tabela; nenhuma linha de outra captura;
- `sync_run_id = 285`, `status = success`, e o histórico **31/31 preservado**;
- nulos e zeros preservados exatamente como no banco;
- ao final: sessão ainda read-only, zero lock exclusivo em `marts`, contagens
  inalteradas (7 e 24) e um único run desta fonte. **Zero escrita.**


## 14. Página de referências externas — AVH-4B-S Task 2/2

**Estado: CONCLUÍDO.** Página read-only, sem migration, sem snapshot, sem
automação e sem escrita em banco. **O deploy do backend no Render é ação do
proprietário** (§14.9).

### 14.1 Rota e navegação

```
/referencias-externas/avoe
```

Grupo de navegação **próprio**, `Referências externas`, com um item (`Avoe
Hub`). Não é um item dentro de Cockpits nem de Inteligência: a separação na
navegação é a primeira barreira contra alguém ler estes números como oficiais.
Os números **não** aparecem em `/canais` nem na Gerencial.

Arquivos: [apps/web/app/referencias-externas/avoe/page.tsx](../apps/web/app/referencias-externas/avoe/page.tsx),
[apps/web/src/lib/avoe-snapshot-contract.ts](../apps/web/src/lib/avoe-snapshot-contract.ts),
`fetchAvoeSnapshot` em [apps/web/src/lib/api-client.ts](../apps/web/src/lib/api-client.ts),
grupo em [apps/web/src/components/shell/nav-config.ts](../apps/web/src/components/shell/nav-config.ts).

### 14.2 Quatro blocos

1. **Proveniência** — fonte Avoe Hub com selo permanente "Fonte externa e
   manual", `captured_at` em horário de São Paulo, idade da captura,
   `sync_run_id`, status da auditoria, o vínculo temporal declarado, o
   `snapshot_id` rotulado como hash de conteúdo, as contagens de metas e canais,
   e a moeda com o estado "assumida (BRL), não confirmada".
2. **Avisos** — lista simples, **sem acordeão e sem tooltip**. Começa pelos
   cinco textos que a tela é obrigada a dizer (não é KPI, sem automação,
   definição não confirmada, moeda assumida, sem realizado) e acrescenta todos
   os `warnings` e `notes` da API que ainda não estejam cobertos, deduplicados
   por texto normalizado. Um aviso novo do backend aparece mesmo que esta versão
   da tela não o conheça. Abaixo, as **notas de cobertura** documentadas no §6:
   Denavita/GoCase como referência externa com atingimento não calculável, e a
   cobertura parcial da Ápice.
3. **Metas** — competência, marca, meta, moeda com selo `assumida` e a data de
   registro na origem.
4. **Canais adicionais** — competência, marca, canal com selo `proxy`, **"Valor
   informado pela Avoe"**, dias cobertos, cobertura (`Mês parcial`/`Mês
   completo`) e a janela de datas.

### 14.3 Filtros

Três filtros **client-side**, sobre as linhas já recebidas: competência, marca
e canal. O de canal só existe na tabela de canais — metas não têm canal, e um
filtro de canal reduzir a lista de metas seria mentira.

Nenhuma nova chamada à API e nenhuma agregação entre linhas: o filtro devolve
um subconjunto das mesmas referências de objeto, na ordem recebida. Provado por
teste (contagem de requisições = 1 antes e depois de filtrar) e no QA.

### 14.4 Estados

| Estado | O que a tela mostra |
|---|---|
| `loading` | `aria-busy` + `aria-live`, texto "Carregando o último snapshot…", nenhuma tabela |
| `available` | os quatro blocos completos |
| `unavailable` | HTTP **200**, o `unavailable_reason` em texto humano, **nenhuma tabela e nenhum filtro**, e a frase "ausência de captura não é o mesmo que valores iguais a zero" |
| `error` | `role="alert"`, texto factual ("a consulta é somente leitura e nada foi alterado"), botão "Tentar novamente" de 44px, **sem** código HTTP e **sem** detalhe técnico |
| listas vazias após filtro | "Nenhuma linha para os filtros escolhidos" — estado de filtro, não de indisponibilidade |
| captura antiga | a partir de **30 dias**, aviso próprio no topo da proveniência; a tabela continua sendo exibida |

`unavailable` vem do **contrato** (`meta.status`), não de lista vazia: um 200
com `available` e arrays vazios continua `available`, porque é dado real vazio.

### 14.5 null versus zero

`null` → **"Não informado"**, com marcador próprio no DOM. Zero informado →
**"R$ 0,00"**. Nenhum caminho converte um no outro, e não existe `?? 0` nem
`|| 0` na página — há teste que falha se aparecer. O mesmo vale para
`brand_key`, `currency_warning` e `source_recorded_at`.

Valores monetários saem **integrais, com centavos**, sem abreviação em K/M: o
`fmtBrl` do projeto abrevia acima de mil, e isso apagaria o centavo que a
reconciliação com o snapshot usa.

### 14.6 Nenhum total, atingimento, margem ou comparação

Não existe função de soma, `reduce`, `calcMoM`, total, subtotal, `tfoot`, linha
de total, atingimento, margem, variação ou comparação — nem no contrato do
frontend, nem na página. A tabela de canais fecha com a frase que explica a
ausência: somar estes valores entre si ou com o GMV oficial produziria um
número que não existe em fonte nenhuma.

O valor de canal se chama **"Valor informado pela Avoe"** e nunca GMV, receita,
venda, resultado, faturamento ou realizado. Os termos aparecem na tela apenas
dentro das frases que **negam** a existência deles — e o QA valida isso nos
cabeçalhos, não no texto corrido, justamente para não apagar a negação.

### 14.7 A UI só apresenta

Nenhuma regra do backend é reimplementada. A página não conhece
`max(captured_at)`, `started_at`, `finished_at`, `import_run_id`,
`source_file` nem `source_file_hash` — há teste que falha se algum aparecer. Os
tipos do contrato do frontend foram conferidos campo a campo contra o OpenAPI
local.

### 14.8 Validação executada

| # | Passo | Resultado |
|---|---|---|
| 1 | `node --test tests/avoe-snapshot.test.ts` | **46 passaram** |
| 2 | `npm test` (suíte web completa) | **1.505 passaram, 0 falharam** |
| 3 | `npm run typecheck` | sem erro |
| 4 | `npm run build` | compilou; a rota sai como estática, 4 kB |
| 5 | QA de navegador (desktop 1440, tablet 768, mobile 390) | **216 verificações, 0 falhas** |
| 6 | reconciliação da API com o snapshot 285 | aprovada |

Dois pinos literais foram atualizados **conscientemente**, como o precedente do
Gate PMA-3 exige: a lista de grupos e rotas de `nav-config.test.ts` e a
contagem de `fetchX` públicas em `request-freshness.test.ts` (24 → 25).

QA cobriu: navegação, filtros nos três viewports, rolagem interna das tabelas,
**zero overflow horizontal da página**, texto ≥ 12px e alvos ≥ 44×44 **no
conteúdo da página**, teclado e foco, os cinco estados, null versus zero,
legibilidade dos avisos, moeda assumida, ausência de total consolidado e zero
erro de console ou hidratação.

O estado `available` usou a **API real em leitura**; `unavailable`, `error`,
captura antiga e o par null/zero usaram **interceptação de rota no navegador** —
o banco não foi tocado nesses casos.

**Dívida pré-existente registrada, fora desta tela:** a navegação do shell tem
14 textos abaixo de 12px e 12 alvos abaixo de 44px, medidos em `/canais`, que
este gate não alterou. Não foi introduzida aqui e não foi corrigida aqui.

### 14.9 Como o proprietário publica o backend no Render

O frontend já está publicado pelo pipeline normal. O **backend** precisa de uma
ação sua, porque este gate não faz deploy:

1. abra o serviço da **API** no dashboard do Render (o mesmo que serve
   `/api/v1/performance/*`);
2. em **Manual Deploy**, escolha **Deploy latest commit** e confirme que o SHA
   é o do commit desta frente ou posterior;
3. **não é necessário** rodar migration: a `015` já está aplicada no Neon desde
   o AVH-4B-P, e este gate não traz nenhuma;
4. **não é necessária** variável de ambiente nova: o endpoint usa a
   `DATABASE_URL` que já existe;
5. quando o deploy terminar, valide com
   `GET /api/v1/performance/avoe-snapshot` — a resposta esperada é HTTP 200 com
   `meta.status = "available"`, `sync_run_id = 285`, `targets_count = 7` e
   `channel_rows_count = 24`;
6. abra `/referencias-externas/avoe` na Torre. Se a página mostrar o estado de
   erro, o deploy da API ainda não propagou; se mostrar `unavailable`, a API
   está no ar e o que falta é dado — nesse caso o `unavailable_reason` na tela
   diz o motivo.

Enquanto o deploy não acontecer, a página mostra o estado de erro em produção,
com o texto factual e o botão de nova tentativa. Nenhuma outra rota é afetada.
