# Snapshots manuais da Avoe — fonte, contrato e runbook

Gate AVH-4A · corrigido no AVH-4A-R · 2026-09-08

> **Estado: NÃO PUBLICADO E NÃO OPERACIONAL.**
> Esta rodada entregou a fundação: especificação DDL, migration, contrato de
> leitura, importador e testes. **Nada foi aplicado no Neon, no Data Mart ou em
> produção, e não há API nem tela.**
>
> O que **foi** executado, num **PostgreSQL 16.14 descartável** criado fora do
> repositório e destruído no fim: a cadeia Alembic até `015`, o `downgrade`, o
> `upgrade` de volta, o primeiro `--apply` com o snapshot real, um segundo
> `--apply` com run id diferente, e os três casos negativos. Ver §11.

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
próprio** (`source_name = 'avoe_manual_snapshot'`):

1. `running` **antes** da transação de dados;
2. `success` ou `failed` **depois**.

Isso significa que **uma tentativa revertida deixa rastro** — o desenho anterior,
de transação única, perdia o registro no rollback e só conseguia gravar sucessos.
Verificado no PostgreSQL descartável: a variante de conteúdo divergente produziu
`sync_run_id = 3` com `status = failed` e mensagem sanitizada.

Garantias adicionais: o `UPDATE` da auditoria exige `rowcount == 1`; `status` é
validado contra `{running, success, failed}`; a mensagem passa pelo sanitizador
que suprime qualquer texto contendo DSN, senha, apikey ou JWT.

**Commit indeterminado não é marcado `failed`.** Se a exceção acontecer no
próprio `commit` dos dados, não se sabe se algo foi gravado — o registro
permanece `running` com `error_message` começando em `INDETERMINADO:`. Afirmar
`failed` seria afirmar que nada entrou, e isso não se sabe.

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

Qualquer exceção dispara `rollback()` da transação inteira — inclusive falha de
import do driver, que é feito **dentro** do `try` justamente por isso. Não há
estado parcial possível: ou as duas tabelas recebem a captura, ou nenhuma
recebe.

Se for necessário desfazer uma captura já comitada, **não há caminho pelo
importador** (não existe DELETE). A remoção é operação manual deliberada, fora
deste runbook, e exige autorização própria — a imutabilidade é a proteção, não
um obstáculo a contornar.

Reverter o schema: `alembic downgrade 014` remove apenas os dois objetos criados
pela `015`. Nenhum objeto oficial é tocado.

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

O que **não** foi validado em banco real: o caminho de commit indeterminado
(§8.2.1) — ele exige uma falha no próprio `commit`, que não é reproduzível sem
injetar defeito no driver. Está coberto apenas por contraprova com conexão
falsa.
