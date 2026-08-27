# DQ-TK1 — Refresh histórico do TikTok Daily

> **Nenhum dado foi reprocessado.** O mecanismo descrito aqui está implementado e
> testado **contra fakes**, e **nunca executado contra Postgres real** — nem em
> dry-run. O Gate DQ-TK1 **não está concluído**, os dados do TikTok Daily
> **continuam na definição antiga** no destino, a auditoria da fonte segue
> **BLOCKED** (§5) e a janela de manutenção **não está autorizada**.

## 1. Por que existe um mecanismo separado

O pipeline diário (`pipelines/ingestion/daily_performance.py`) grava por **UPSERT
puro**: `INSERT ... ON CONFLICT (date, loja_id, marketplace_id) DO UPDATE`.
Atualiza o que existe, insere o que falta e **nunca remove**.

Depois da correção do contrato comercial (`b43ffbe`), isso deixa um defeito
concreto: uma chave `(dia, loja)` que **deixe de existir** na nova extração — por
exemplo um dia/marca cujo único pedido era `CANCELLED` — permanece no destino
com o **valor antigo**, indefinidamente e sem sinal. Chave órfã é o que o
`UPSERT` estruturalmente não repara, e é por isso que o refresh **apaga a janela
antes de inserir**.

`pipelines/ops/refresh_tiktok_daily_contract.py` é operação de **manutenção**,
deliberadamente fora do fluxo diário: escopo explícito, backup obrigatório,
reconciliação antes do commit e caminho de restore.

## 2. NO-GO anterior (Fase C, Task 1/2)

A tentativa anterior terminou corretamente em **NO-GO**, sem nenhuma escrita, por
cinco razões:

1. o backfill disponível era UPSERT puro e não removia chaves órfãs;
2. não existia rollback pós-commit nem backup verificável;
3. o checkout operacional estava em `76f361b`, com a definição **antiga**;
4. o Scheduler executaria o código antigo e desfaria parte do backfill;
5. o túnel do Data Mart estava desligado, impedindo validar a fonte.

## 3. Janela

| Item | Valor | Natureza |
|---|---|---|
| Início padrão | **2025-12-26** | primeiro dia **já existente** no destino |
| Fim | **D−1** em `America/Sao_Paulo`, capturado **uma única vez** no início | dia corrente é recusado |
| Extensão candidata | **2025-10-05 .. 2025-12-25** | **decisão separada** — apenas medida e reportada |

⚠️ **A extensão anterior a 26/12 NÃO faz parte do backfill aprovado.** O
mecanismo **recusa** `date_from` anterior a `2025-12-26` com erro explícito:
estender o histórico da Torre é decisão de negócio, nunca efeito colateral de um
refresh. `date_to` posterior a D−1 também é recusado — o dia corrente está aberto
e seus status ainda maturam (mediana de 5,1 dias até `DELIVERED`), então publicá-lo
como definitivo gravaria número que muda sozinho.

## 4. Contrato do mecanismo

O contrato comercial **não é redefinido**: `connector.QUERY` é reutilizada
verbatim, com as mesmas constantes (`COMMERCIAL_ORDER_STATUSES`,
`KNOWN_ORDER_STATUSES`, `BRANDS_IN_SCOPE`), e o mapeamento marca → `loja_id` vem
de `transforms.tiktok_brand_daily`. Uma segunda definição de GMV divergiria da
primeira no dia em que uma delas mudasse.

- **Dry-run por padrão** — e o dry-run **lê fonte E destino** para dizer o que
  mudaria (ver §4.1). Escrita só com `--apply`, que exige `--run-id` **e**
  `--backup-dir` explícitos.
- **`marketplace_id = 1` fixo**, constante do módulo — não é flag do CLI. ML e
  Shopee são inalcançáveis por construção.
- **Fotografia** da fonte em `REPEATABLE READ` + `READ ONLY`, materializada em
  memória; toda reconciliação compara contra ela, nunca contra uma releitura.
- **Pré-condições antes de qualquer DML:** fonte não vazia, cinco marcas **no
  conjunto da janela**, todo dia com alguma linha, zero chave duplicada, zero
  status desconhecido/nulo, zero `total_amount` nulo em pedido comercial.
- **Reconciliação exata** antes do commit: chaves, linhas, agregados Decimal,
  `EXCEPT` bidirecional e ausência de linha fora da janela. Qualquer divergência
  levanta erro e provoca **rollback integral**.
- Erros sanitizados; sem retry, sleep, backoff ou agendamento; nada executado no
  import; nenhuma migration e nenhuma tabela permanente de staging.

⚠️ **Não se impõe produto cartesiano de cinco marcas × todos os dias.** Uma
combinação marca × dia ausente pode ser **ausência legítima de pedidos**, e não há
prova comercial de que toda marca venda todo dia. Fabricar linha zero para
preencher inventaria medição. As combinações ausentes são **contadas e
reportadas** — no dry-run e nas pré-condições — para revisão humana.

**Nulo permanece nulo.** No CSV, `None` é serializado como campo vazio e volta
como `None`; ausência nunca vira zero, e um agregado sem nenhum valor presente é
gravado como `null` no manifesto e produz delta `null`, não `0`.

### 4.1 Dry-run — o que ele faz

Lê a fonte (`REPEATABLE READ` + `READ ONLY`) **e** o destino (sessão
explicitamente read-only). **Zero** advisory lock, **zero** lock de tabela,
**zero** backup, **zero** DDL/DML. Reporta:

- linhas atuais e novas;
- chaves **inseridas**, **removidas/órfãs**, **alteradas** e **inalteradas** — as
  quatro classes são disjuntas e sua soma fecha com a união das chaves;
- agregados antigos e novos, e os **deltas Decimal**;
- GMV, pedidos, ticket e cancelados **por mês e por marca**;
- min/max e cobertura das duas fotografias;
- combinações marca × dia ausentes.

### 4.2 Duas listas de colunas — 44 escritas, 46 no backup

A tabela tem **46** colunas: as 44 canônicas mais `id` (surrogate) e
`ingested_at` (carimbo de carga).

| Lista | Colunas | Uso |
|---|---|---|
| `WRITE_COLUMNS` | **44** | o que o refresh **insere**. `id` vem da sequence e `ingested_at` do `DEFAULT`, exatamente como no pipeline diário |
| `BACKUP_COLUMNS` | **46** | o que o backup **guarda** e o restore **recoloca** |

⚠️ **Sem `id` e `ingested_at` o restore não seria uma restauração.** Geraria
linhas novas — outro `id` e um `ingested_at` de agora —, falsificando o "quando
isto foi carregado" da Torre. O restore os reinsere **explicitamente**.

**Não se executa `setval`:** a sequence já avançou, e fazê-la retroceder
arriscaria colisão futura de `id`.

### 4.3 Backup — CSV + manifesto + `SHA256SUMS`

CSV com as **46** colunas + manifesto (janela, contagens, datas, `loja_id`s,
agregados Decimal) + companion **`SHA256SUMS`** cobrindo **os dois arquivos**.
Publicação atômica por `os.link` (nunca sobrescreve) e **releitura obrigatória do
disco** antes do primeiro DML.

**O companion cobre CSV e manifesto, não só o CSV.** Um companion que cobrisse
apenas o CSV deixaria alteração do manifesto — inclusive `date_from`/`date_to`,
`row_count` e os agregados — passar sem ser detectada. `load_backup` exige
**exatamente** os dois nomes (extra, ausente ou duplicado reprova), verifica os
**hashes primeiro** e só então lê o conteúdo.

⚠️ **O que o `SHA256SUMS` garante, e o que não garante.**

- **Garante:** detecção de **corrupção ou alteração** do CSV e do manifesto,
  enquanto o próprio companion permanecer confiável.
- **NÃO garante autenticidade criptográfica:** não há assinatura nem HMAC. Quem
  tiver capacidade de alterar os três arquivos — CSV, manifesto e `SHA256SUMS` —
  pode **recalcular os hashes** e passar por essa camada.

O que continua valendo independentemente dos hashes são as **validações
semânticas**, que reprovam inconsistências:

| Validação | Fonte da verdade |
|---|---|
| agregado declarado ≠ recalculado a partir do CSV | o próprio CSV |
| janela do manifesto ≠ `--date-from`/`--date-to` | **o operador**, fora dos arquivos |
| `marketplace_id` diferente de 1 | constante do módulo |
| chave `(date, loja_id, marketplace_id)` duplicada | contagem na staging |
| linha fora da janela, ou linha sem `id` | contagem na staging |

Dessas, a única cuja fonte da verdade **não está nos arquivos** é a janela
informada pelo operador — é ela que impede o próprio backup de decidir o que
será apagado.

**Modelo operacional pressuposto:** o diretório de backup é local e controlado
pelo proprietário. Este mecanismo protege contra corrupção acidental, edição
descuidada e backup incompleto ou trocado — não contra um adversário com escrita
arbitrária no diretório, que poderia reescrever todos os artefatos de forma
coerente.

**`restore --apply` exige `--date-from` e `--date-to` explícitos**, que precisam
coincidir com o manifesto: a janela apagada não pode ser decidida pelo próprio
arquivo.

### 4.4 Ordem transacional — e por que o backup fica dentro dela

O advisory lock só exclui **outras instâncias deste CLI**. O
`daily_performance.py` não o conhece e escreve na mesma tabela. Ler o estado
anterior em autocommit e só depois abrir a transação deixaria uma janela em que
outro writer poderia mudar a linha — e o backup deixaria de representar o estado
imediatamente anterior à publicação.

```
advisory lock -> BEGIN -> timeouts -> LOCK TABLE -> le as 46 colunas
  -> backup gravado e RELIDO -> DELETE -> INSERT -> reconciliacao
  -> commit -> advisory unlock
```

O lock de tabela é **`SHARE ROW EXCLUSIVE`**: o **menor** nível que conflita com
`ROW EXCLUSIVE` — o que `INSERT`/`UPDATE`/`DELETE` adquirem — e portanto bloqueia
DML concorrente, **sem** conflitar com `ACCESS SHARE`, de modo que `SELECT` comum
(inclusive o da API) continua passando. `ACCESS EXCLUSIVE` bloquearia leitura
também e seria desnecessariamente agressivo.

Se o backup falhar, a transação termina **sem nenhum `DELETE`/`INSERT`**: o
primeiro DML no fato é o `DELETE`, e ele só ocorre depois da validação integral
do backup. O **restore adquire o mesmo lock de tabela** antes de tocar o fato.
Nenhuma segunda conexão gravável é aberta em nenhum caminho.

## 5. Auditoria da fonte — BLOCKED

⚠️ **A auditoria da fonte (Fases 1 e 3) NÃO foi concluída.** O túnel read-only
para a réplica não pôde ser estabelecido:

```
debug1: Server host key: ssh-ed25519 SHA256:wjetle9YPNc9D0nV6m6pDQzTgy6ThW0WU3BISJrPSV4
@    WARNING: REMOTE HOST IDENTIFICATION HAS CHANGED!     @
Host key verification failed.
```

A chave de host do bastion **mudou** em relação ao registrado em `known_hosts`.
Não foi feito bypass de `StrictHostKeyChecking`, `known_hosts` não foi alterado, e
nenhum host alternativo foi tentado por adivinhação. O processo SSH iniciado saiu
sozinho por `ExitOnForwardFailure=yes` e nenhum processo alheio foi encerrado.

**Ação do proprietário**, depois de verificar o fingerprint acima por canal
independente (console do provedor, não pelo próprio SSH):

```powershell
ssh-keygen -R <IP_DO_BASTION>          # remove a entrada obsoleta
ssh -i $env:USERPROFILE\.ssh\datamart_bastion `
    -o StrictHostKeyChecking=ask <USUARIO>@<IP_DO_BASTION>   # confirma a nova
# depois, o forward read-only:
ssh -N -i $env:USERPROFILE\.ssh\datamart_bastion `
    -L 127.0.0.1:15432:<HOST_INTERNO>:<PORTA_INTERNA> `
    -o ExitOnForwardFailure=yes -o StrictHostKeyChecking=yes `
    -o ServerAliveInterval=30 <USUARIO>@<IP_DO_BASTION>
```

Sem essa auditoria continuam **não medidos**: cobertura diária da Raw na janela,
duplicidades antes/depois da dedup, os oito status conhecidos, fingerprints das
duas amostragens separadas, deltas antigo × novo por mês e por marca, e a
contagem de chaves inseridas/atualizadas/**removidas**.

## 6. Pré-condições da Task 2/2

Duas, e nenhuma delas foi tocada nesta rodada:

1. **Checkout operacional** — o clone da máquina que o Scheduler executa, na raiz
   do worktree `main` — está em `76f361b`, com a definição **antiga**, e tem
   resíduos preexistentes não commitados. Precisa avançar por fast-forward seguro
   até o commit que contenha `b43ffbe` **e** o mecanismo aprovado.
2. **Scheduler** `mktplace_full_daily` está **Pronto**, próxima execução
   `27/08/2026 06:00:00`. Se rodar com o código antigo depois do backfill,
   desfaz a janela recente (lookback de 10 dias) e reintroduz a definição antiga
   nos dias mais novos.

## 7. Sequência da janela de manutenção (não executada)

1. desabilitar temporariamente **apenas** `mktplace_full_daily`;
2. provar zero processo e zero lock;
3. atualizar o checkout operacional por fast-forward seguro até o commit que
   contenha `b43ffbe` **e** o mecanismo — **parar** se os resíduos impedirem
   (nunca `reset` nem `clean`);
4. validar o checkout operacional;
5. criar o backup final **imediatamente antes** da escrita;
6. executar **uma única** tentativa de backfill (`--apply --run-id ...`);
7. reconciliar e provar isolamento de ML/Shopee;
8. reabilitar o Scheduler;
9. observar **uma** execução agendada e confirmar que os dez dias recentes não
   voltaram à definição antiga.
