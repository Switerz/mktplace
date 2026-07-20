# Automacao Shopee -> Data Mart

Documento de operacao e handoff da automacao Shopee em
`C:\Users\User\Documents\relatorio_mercos\shopee_automacao`.

Data deste estado: 2026-07-17.

Este documento explica como o projeto esta funcionando hoje, desde o scraping no
Seller Center da Shopee ate a insercao no Data Mart nas camadas Raw e Silver.
Ele foi escrito para que um colaborador, junto com a IA dele, consiga entender o
fluxo completo antes de mexer em multi-marca, Gold ou Neon.

## Resumo executivo

A automacao baixa tres relatorios da Shopee para a marca atualmente configurada
como `kokeshi`:

- Pedidos Gerais;
- Informacoes Gerais da loja;
- Shopee Ads.

Depois do download, os arquivos sao publicados em um lote local padronizado,
validados pelo loader oficial do repositorio `Switerz/mktplace`, inseridos nas
tabelas Raw do Data Mart e transformados para as tabelas Silver.

O fluxo Raw/Silver esta funcional e foi testado com insert real no banco. O
Gold/Neon ainda nao esta ligado de proposito, porque a Shopee trabalha com
exports moveis/sobrepostos, e o incremental Gold atual do `mktplace` ainda nao
reprocessa corretamente janelas recentes ja carregadas.

## Contexto rapido para outra IA

Se voce e uma IA recebendo este projeto de um colaborador, leia esta secao antes
de propor mudancas.

O objetivo atual nao e reescrever a automacao. O objetivo e continuar a partir
de um estado ja funcional: a marca `kokeshi` ja baixa os relatorios, publica um
lote padronizado, valida com o loader oficial do `mktplace`, insere no Raw do
Data Mart e transforma para Silver com reconciliacao final.

O que ja esta comprovado:

- a conexao com o banco funciona;
- as roles dedicadas existem;
- o checkout `.mktplace` esta fixado no commit esperado;
- o fluxo Raw/Silver inseriu um lote real de `kokeshi`;
- a reconciliacao Raw/Silver ficou com zero diferenca;
- os testes automatizados locais passaram.

O que voce nao deve assumir:

- nao assuma que Gold/Neon podem ser ligados agora;
- nao assuma que as outras marcas ja conseguem baixar sozinhas;
- nao assuma que o mesmo arquivo pode ser reinserido sem olhar hash/idempotencia;
- nao copie regras SQL do `mktplace` para dentro desta automacao;
- nao use credencial admin para rotina diaria;
- nao exponha conteudo de `.env` ou senhas em logs, commits ou resposta.

O ponto mais importante: esta automacao local e uma "ponte operacional". A fonte
da verdade das regras Raw/Silver e o repositorio `Switerz/mktplace`, dentro de
`.mktplace/`. Se uma regra de negocio precisar mudar, a mudanca ideal deve
nascer no `mktplace` e depois a automacao deve apenas consumir a nova versao.

Se voce for continuar o trabalho, siga esta ordem:

1. Rode os diagnosticos (`preflight`, `inspect`, testes).
2. Entenda `shopee_pipeline.py` e `shopee_datamart.py`.
3. Para multi-marca, parametrizar primeiro a marca e a sessao Shopee.
4. Testar cada marca com `validate` antes de escrever.
5. So depois habilitar Raw/Silver por marca.
6. Deixar Gold/Neon para uma etapa separada no `mktplace`.

Uma boa primeira tarefa para outra IA e transformar `MARCA = "kokeshi"` em uma
configuracao por ambiente, por exemplo `SHOPEE_MARCA`, e separar
`SHOPEE_STATE_FILE` por marca. Isso permite rodar uma marca por vez sem misturar
sessao, arquivo e brand.

## Pasta em escopo

Toda a alteracao feita aqui esta restrita a:

```text
C:\Users\User\Documents\relatorio_mercos\shopee_automacao
```

Arquivos principais:

- `shopee_download.py`: baixa os relatorios no Seller Center;
- `shopee_pipeline.py`: orquestra download, validacao e carga no Data Mart;
- `shopee_datamart.py`: integra a automacao local com o repositorio `mktplace`;
- `rodar_shopee.bat`: entrada operacional para rodar a automacao;
- `config.example.py`: template de configuracao local;
- `README.md`: documentacao historica e notas resumidas;
- `shopee_datamart_daily_jobs_handoff.md`: handoff tecnico vindo do fluxo
  `mktplace`;
- `.mktplace/`: checkout local do repositorio `Switerz/mktplace`, ignorado pelo
  Git desta automacao;
- `data/datamart_batches/`: lotes publicados para validacao/carga;
- `tests/`: testes automatizados da integracao Data Mart.

## Dependencia do mktplace

O repositorio `Switerz/mktplace` foi clonado localmente dentro de:

```text
.mktplace/
```

Ele esta fixado no commit:

```text
f32c52cb538a8bac339f5bf4ea640bee8b203dea
```

O arquivo `shopee_datamart.py` sempre confere esse commit antes de chamar os
artefatos oficiais. Isso evita rodar uma versao diferente do contrato sem
perceber.

Importante: regras SQL e regras de transformacao nao foram copiadas para a
automacao. A automacao chama o codigo versionado do `mktplace`.

## Segredos e credenciais

Os secrets de banco ficam dentro de `.mktplace/` e sao ignorados pelo Git:

- `.mktplace/.env`: URL read-only para inspecao;
- `.mktplace/.env.shopee-write.local`: URL dedicada para escrita Raw;
- `.mktplace/.env.shopee-silver-write.local`: URL dedicada para escrita Silver.

Esses arquivos nao devem ser commitados, enviados em print ou colados em chat.

No banco foram usadas roles dedicadas:

- `shopee_datamart_reader`: leitura, com transacao read-only por padrao;
- `shopee_raw_silver_writer`: escrita restrita para Raw/Silver.

A role de escrita recebeu apenas o necessario para inserir:

- `USAGE, CREATE` no schema `raw`, porque o preflight oficial exige criar objeto
  temporario/teste no schema;
- `USAGE` no schema `silver`;
- `SELECT, INSERT` nas tabelas Raw/Silver alvo;
- `USAGE, SELECT` nas sequences Raw usadas pelos IDs.

Ela nao recebeu `UPDATE`, `DELETE` ou `TRUNCATE` nas tabelas operacionais.

## Fluxo completo da automacao

O fluxo operacional atual e:

```text
rodar_shopee.bat
  -> shopee_pipeline.py
     -> shopee_download.py baixa os 3 relatorios
     -> shopee_datamart.publish_batch()
     -> loader oficial Raw em dry-run
     -> loader oficial Raw com --apply --backfill
     -> SQL Silver oficial do mktplace
     -> limpeza dos arquivos antigos locais
```

## Etapa 1: scraping/download

O arquivo `shopee_download.py` usa a sessao salva da Shopee para acessar o Seller
Center sem precisar refazer login todo dia.

O login manual, quando necessario, e feito com:

```bat
rodar_shopee_login.bat
```

A automacao diaria e feita com:

```bat
rodar_shopee.bat
```

Hoje a marca no pipeline esta fixa como:

```python
MARCA = "kokeshi"
```

Portanto, neste momento a automacao completa esta operacional para `kokeshi`.
As outras marcas ja existem no contrato do Data Mart, mas ainda falta
parametrizar download/login/sessao por marca.

## Etapa 2: publicacao do lote

Depois do download, `shopee_pipeline.py` chama:

```python
dm.publish_batch(MARCA, fp, fv, fa)
```

Esse metodo cria um lote dentro de:

```text
data/datamart_batches/<timestamp>-<id>/
```

Com a estrutura esperada pelo loader oficial:

```text
<batch>/
  batch_manifest.json
  kokeshi/
    Order.all.<timestamp>.xlsx
    kokeshi.shopee-shop-stats.<timestamp>.xlsx
    Dados.<timestamp>.csv
```

O `batch_manifest.json` guarda metadados seguros do lote, como:

- marca;
- paths relativos;
- hashes SHA-256;
- tamanho dos arquivos.

Ele nao guarda payload sensivel de pedido.

## Etapa 3: padronizacao dos exports

Antes de publicar o XLSX de pedidos no lote, a automacao padroniza alguns pontos
que vieram diferentes do contrato Silver.

O arquivo original baixado da Shopee fica intacto. A padronizacao acontece na
copia publicada dentro de `data/datamart_batches/`.

Padronizacoes aplicadas:

| Campo vindo da Shopee | Padrao exigido pelo contrato |
|---|---|
| `Shopee Owned = True/False` | `TRUE/FALSE` |
| `Desconto do vendedor.1` | `Desconto do vendedor__col26` |
| `Cidade.1` | `Cidade__col59` |

Essas regras ficam em `shopee_datamart.py`, na funcao `_publish_orders_file`.

Motivo: o SQL Silver do `mktplace` valida dominio e schema drift antes de
inserir. Sem essas padronizacoes, o Raw aceita o arquivo, mas o Silver aborta
corretamente para evitar staging inconsistente.

## Etapa 4: dry-run oficial Raw

Antes de escrever no banco, a automacao chama o loader oficial do `mktplace` em
modo dry-run:

```text
python -m pipelines.ingestion.load_shopee_raw --dry-run --data-path <batch>
```

Esse dry-run valida:

- se os arquivos sao legiveis;
- quantidade de linhas parseadas;
- linhas rejeitadas/vazias;
- erros numericos;
- headers;
- auditoria de PII;
- reconciliacao basica.

Se o dry-run falhar, a automacao nao escreve no banco e envia alerta.

## Etapa 5: insert Raw

Com o dry-run aprovado, a automacao chama:

```text
python -m pipelines.ingestion.load_shopee_raw --apply --backfill --data-path <batch>
```

Isso insere nas tabelas Raw:

- `raw.shopee_ingestion_file`;
- `raw.shopee_order_item_export`;
- `raw.shopee_shop_stats_export`;
- `raw.shopee_ads_export`.

O loader usa hash e manifesto para idempotencia. Se o mesmo arquivo ja existir,
ele deve pular em vez de duplicar.

Observacao tecnica: o loader oficial atualmente pode retornar exit code `9`
mesmo quando o backfill do lote terminou com `0 falharam` e a reconciliacao final
mostra `manifesto == linhas-filhas`. O wrapper local aceita somente esse caso
especifico. Qualquer outro exit code ou output com falhas continua derrubando a
automacao.

## Etapa 6: transformacao Silver

Depois do Raw, a automacao roda o SQL oficial:

```text
.mktplace/db/sql/staging/shopee_staging_transform.sql
```

Esse SQL transforma Raw para:

- `silver.stg_shopee_order_item_snapshots`;
- `silver.stg_shopee_shop_stats`;
- `silver.stg_shopee_ads`.

Ele faz validacoes pre-insert e aborta se encontrar:

- campo obrigatorio vazio;
- valor fora de dominio;
- numero invalido;
- data invalida;
- schema drift;
- divergencia entre Raw e manifesto.

Adaptacao local importante: o SQL oficial usa `LOCK TABLE ... IN SHARE MODE`.
Para manter a role de automacao sem `UPDATE`, o wrapper troca esse lock para
`ACCESS SHARE MODE` em memoria antes de executar. A regra de negocio e os
inserts continuam vindo do SQL oficial.

## Estado atual do banco apos o piloto

Foi feito um piloto real com a marca `kokeshi`.

Lote final inserido:

```text
20260717T155433Z
```

Arquivos/linhas:

- `kokeshi/Dados.20260717T155433Z.csv`: 76 linhas;
- `kokeshi/kokeshi.shopee-shop-stats.20260717T155433Z.xlsx`: 31 linhas;
- `kokeshi/Order.all.20260717T155433Z.xlsx`: 22.473 linhas.

Contagens finais observadas:

| Tabela | Linhas |
|---|---:|
| `raw.shopee_ingestion_file` | 123 |
| `raw.shopee_order_item_export` | 405.771 |
| `raw.shopee_shop_stats_export` | 811 |
| `raw.shopee_ads_export` | 880 |
| `silver.stg_shopee_order_item_snapshots` | 405.771 |
| `silver.stg_shopee_shop_stats` | 811 |
| `silver.stg_shopee_ads` | 880 |
| `gold.marketplace_region_daily` | 33.978 |

Reconciliacao final Raw/Silver:

| Fonte | Missing in Silver | Extra in Silver |
|---|---:|---:|
| orders | 0 | 0 |
| shop_stats | 0 | 0 |
| ads | 0 | 0 |

Gold foi apenas inspecionado. Ele nao foi atualizado por esta automacao.

## Como rodar

Para rodar o pipeline operacional:

```bat
rodar_shopee.bat
```

O `.bat` define:

```bat
set SHOPEE_DATAMART_WRITE=1
```

Isso habilita a escrita Raw/Silver mesmo que `DATAMART_WRITE_ENABLED` no
`config.py` esteja falso ou ausente.

Tambem e possivel controlar via `config.py`:

```python
DATAMART_WRITE_ENABLED = True
```

O recomendado para operacao atual e usar o `.bat`, porque ele deixa explicito
que a execucao diaria escreve no Data Mart.

## Comandos uteis de diagnostico

Conferir se o checkout do `mktplace` esta no commit esperado:

```powershell
.\.mktplace\.venv\Scripts\python.exe shopee_datamart.py preflight
```

Inspecionar o banco em read-only:

```powershell
.\.mktplace\.venv\Scripts\python.exe shopee_datamart.py inspect
```

Validar um lote manual sem escrever:

```powershell
.\.mktplace\.venv\Scripts\python.exe shopee_datamart.py validate `
  --brand kokeshi `
  --orders exemplos\kokeshi_pedidos_gerais_20260625_1410.xlsx `
  --shop-stats exemplos\kokeshi_informacoes_gerais_20260625_1411.xlsx `
  --ads exemplos\kokeshi_ads_dados_gerais_20260625_1412.csv
```

Rodar testes:

```powershell
.\.mktplace\.venv\Scripts\python.exe -m pytest -q
```

Compilar arquivos principais:

```powershell
.\.mktplace\.venv\Scripts\python.exe -m py_compile shopee_datamart.py shopee_pipeline.py
```

## Testes automatizados

Existe uma suite em:

```text
tests/test_shopee_datamart.py
```

Ela cobre:

- contrato de nomes/pastas do lote;
- rejeicao de marca fora do contrato;
- checkout fixado do `mktplace`;
- adaptacao do lock Silver para a role restrita;
- padronizacao de `Shopee Owned`, `Desconto do vendedor.1` e `Cidade.1`;
- aceitacao controlada do exit code `9` do loader Raw somente quando o output
  comprova reconciliacao.

Ultimo resultado:

```text
6 passed
```

## O que ainda nao foi feito

### Multi-marca

O contrato do Data Mart aceita:

- `apice`;
- `barbours`;
- `kokeshi`;
- `lescent`;
- `rituaria`.

Mas o pipeline de download ainda esta fixo em `kokeshi`.

Para multi-marca de verdade, ainda precisa:

- parametrizar `MARCA`;
- ter sessao/login separado por marca;
- permitir `SHOPEE_STATE_FILE` por marca;
- garantir pasta de download por marca ou padrao de nomes seguro;
- rodar uma marca por vez, ou criar um loop controlado;
- evitar misturar arquivos de uma conta com o `brand` de outra.

Exemplo de alvo futuro:

```bat
set SHOPEE_MARCA=barbours
set SHOPEE_STATE_FILE=shopee_state_barbours.json
set SHOPEE_DATAMART_WRITE=1
python shopee_pipeline.py
```

Esse alvo ainda nao esta implementado.

Para uma IA implementando multi-marca, o raciocinio correto e:

1. O Data Mart ja aceita varias marcas no campo `brand`.
2. O wrapper `publish_batch()` tambem ja aceita as marcas oficiais.
3. O gargalo nao esta no banco; esta no download/login/local state.
4. Cada marca provavelmente precisa de sua propria sessao Shopee salva.
5. O pipeline nao pode usar uma sessao de uma marca e publicar com o nome de
   outra.

Portanto, antes de subir `apice`, `barbours`, `lescent` ou `rituaria`, validar:

- existe login/sessao daquela marca?
- o arquivo baixado realmente pertence a ela?
- o `brand` passado para `publish_batch()` e o mesmo da conta logada?
- o dry-run passa sem erros?
- a carga sera feita uma marca por vez?

Se houver exports manuais das outras marcas ja baixados, e possivel testar sem
mexer no scraping usando o comando `validate` com `--brand <marca>`. Mas cuidado:
se depois rodar `apply` com arquivos historicos alterados/padronizados, eles
podem entrar como novos manifestos se o hash for diferente.

### Gold e Neon

Raw/Silver estao prontos.

Gold/Neon continuam desligados porque o incremental atual do `mktplace` trabalha
por data maxima. A Shopee baixa janelas moveis, entao uma execucao nova pode
alterar dias ja existentes. Antes de ligar Gold, o `mktplace` precisa suportar
refresh por janela sobreposta, por exemplo:

- detectar datas afetadas pelo novo lote Shopee;
- apagar/recriar somente essas datas no Gold;
- sincronizar Neon apenas depois da Gold reconciliada.

Sem isso, Gold poderia ficar incompleto ou defasado em dias recentes.

**Atualizacao 2026-07-17 (Gate S1/S2 do `mktplace`, so leitura/diagnostico):**
auditoria tecnica confirmou a causa raiz acima e propos um contrato de refresh
por janela explicita (`--date-from`/`--date-to`, nunca inferencia automatica —
nao ha metadado hoje que diga com seguranca quais datas um novo arquivo Shopee
afetou). Do lado do `mktplace`, ja existe:

```powershell
python -m pipelines.ingestion.gold_regional.loader `
  --diagnose-shopee-window --date-from YYYY-MM-DD --date-to YYYY-MM-DD
```

Isso e **so um diagnostico, 100% somente leitura** — mostra o que aconteceria
se a janela fosse recalculada (linhas/GMV/orders atuais vs. recalculados da
fonte, deltas, risco de fonte zerada), mas **nao escreve nada**. Ainda **nao
existe** `--refresh-shopee-window` (o comando que de fato apagaria/recriaria
Gold por janela) — isso fica para uma etapa separada, com secret dedicado
novo e autorizacao explicita a parte. Nada muda para esta automacao local: ela
continua restrita a Raw/Silver, e Gold/Neon continuam fora do escopo dela.

**Atualizacao 2026-07-17 (Gate S2.1, endurecimento do diagnostico):** o
diagnose ficou mais confiavel para decidir se uma janela precisa mesmo ser
substituida, sem deixar de ser somente leitura:

- **Snapshot consistente**: todas as consultas rodam numa unica transacao
  read-only `REPEATABLE READ` (mesma conexao, mesmo instante), entao uma
  ingestao concorrente nao consegue mais fazer o diagnostico comparar dois
  estados diferentes do banco.
- **Comparacao exata por chave**: alem de linhas/GMV/orders totais, o
  diagnostico agora compara Gold vs. fonte **campo a campo, por
  (date, marketplace_id, loja_id, uf)**. Isso detecta ate redistribuicao
  entre UFs (ex.: pedidos que saem de "nao identificada" para "SP") que
  nao mexem nos totais. Ele reporta `would_change_data` (a janela precisa
  de refresh?) e `structurally_safe_for_refresh` (a fonte esta sa para
  servir de base a um refresh?).

Continua sem escrever nada, sem secret novo, e o refresh de escrita
(`--refresh-shopee-window`, Gate S3) continua nao existindo.

**Atualizacao 2026-07-17 (Gate S3, refresh/restore transacionais — implementado,
NAO executado):** o `mktplace` agora tem, alem do diagnostico, o comando de
escrita real por janela e o respectivo restore:

```powershell
python -m pipelines.ingestion.gold_regional.loader `
  --refresh-shopee-window --date-from YYYY-MM-DD --date-to YYYY-MM-DD `
  --audit-path <caminho-absoluto-fora-do-repo.json>

python -m pipelines.ingestion.gold_regional.loader `
  --restore-shopee-window --audit-path <backup.json> --expected-backup-sha256 <64-hex>
```

Pontos importantes para quem opera o scraping:

- **Secret dedicado, novo**: `.env.gold-window-write.local` (2 chaves:
  `DATAMART_GOLD_WINDOW_WRITE_URL` e
  `I_UNDERSTAND_THIS_DELETES_GOLD_SHOPEE_WINDOW`) — **nunca** o mesmo secret
  do incremental. Privilegio minimo: so SELECT/INSERT/DELETE na tabela Gold
  regional e SELECT na Silver Shopee, nunca CREATE/UPDATE/TRUNCATE/DROP nem
  acesso a tabelas ML/TikTok.
- **Atualizacao 2026-07-18 (Gate S3.1, revisao de seguranca pre-commit)**: o
  preflight ficou mais rigoroso antes de qualquer piloto real —
  `rds_superuser` agora **bloqueia** (antes era so aviso), o `system_identifier`
  do cluster fisico e **obrigatorio nos dois lados** (sem cair para
  comparacao por database+porta, que era aceitavel no incremental mas nao
  aqui), SSL precisa estar **confirmado** ativo, e CREATE/UPDATE/TRUNCATE na
  Gold viraram **privilegios proibidos** que tambem bloqueiam se a
  credencial os tiver. O backup passou a ser validado **integralmente**, nao
  so pelo SHA-256 (que só garante integridade dos bytes): contagens e
  agregados (GMV/pedidos) declarados sao recalculados a partir dos registros
  e precisam bater exatamente. As validacoes de janela/caminho/hash/JSON
  agora rodam **antes** de ler o secret ou conectar no banco. Nada disso
  mudou o escopo do DELETE nem foi executado de verdade.
- **Atualizacao 2026-07-18 (Gate S3.2, correcao final pre-commit)**: quatro
  lacunas defensivas fechadas — o preflight nunca mais deixa uma falha de
  consulta escapar como erro cru (vira bloqueio sanitizado); toda checagem
  sensivel exige o valor booleano exato ("nao foi possivel confirmar"
  bloqueia literalmente, inclusive nos privilegios proibidos); o backup e
  lido UMA unica vez em binario com limite de tamanho, e hash/JSON sao
  calculados dos mesmos bytes (sem janela para o arquivo mudar entre as
  checagens); e caminhos problematicos nunca viram traceback. Continua sem
  nenhuma execucao real contra o banco.
- **Escopo do DELETE**: sempre e so `marketplace_id=Shopee AND date BETWEEN
  date_from AND date_to` — nunca ML, TikTok, ou Shopee fora da janela.
- **Backup automatico antes de qualquer DELETE**: publicado de forma atomica
  (nunca sobrescreve um backup anterior) com um `.sha256` ao lado, para
  possibilitar o `--restore-shopee-window` depois.
- **NO_OP nao e erro**: se a janela ja estiver reconciliada (Gold == fonte),
  o comando nao apaga/insere nada e retorna sucesso.
- **`--confirm-empty-window`** e uma excecao explicita, so' para o caso raro
  de a fonte recalculada dar zero linhas com a Gold tendo linhas — nao
  desativa nenhuma outra validacao.
- **Restore e compare-and-swap**: só restaura se o estado atual da Gold
  bater exatamente com o que o refresh deixou — se algo mudou a janela
  depois, o restore se recusa a sobrescrever.
- **Nada disso foi executado de verdade nesta rodada** — sem conexao a
  banco, sem secret real criado, sem DDL/DML remoto. Sync Neon continua
  em passo manual separado (`sync_region_daily.py --sync`), so' depois de
  um refresh reconciliado.
- **A automacao externa de scraping NAO deve habilitar este comando ainda**
  — isso fica para o piloto do Gate S4, com credencial dedicada e restrita
  para a pessoa responsavel pelo scraping (nunca o writer generico atual),
  e autorizacao explicita separada.
- **Atualizacao 2026-07-20 (Gate S4.1, preparacao read-only do piloto)**: o
  `mktplace` rodou o diagnose real (somente leitura) contra o Data Mart numa
  janela de controle e em todo junho/2026 (ate a data maxima real da fonte,
  2026-06-25) em blocos de ate 7 dias. Resultado: **todas** as janelas
  testadas ja estao reconciliadas (`would_change_data=False`) — inclusive as
  que cobrem o periodo do lote inserido por esta automacao em 2026-07-17.
  Ou seja, ate agora **nao ha uma janela real disponivel para validar o
  refresh de escrita fazendo diferenca observavel**; isso nao e um erro, so
  significa que a fonte e a Gold ja convergem no que foi auditado. Tambem
  foi feita uma auditoria read-only de permissoes (tabela/sequence Gold
  existem, role dedicada `gold_shopee_window_writer` ainda nao existe) e
  preparado o SQL de criacao dessa role — nada disso foi executado. Continua
  valendo: **nao habilitar `--refresh-shopee-window`** nesta automacao ate o
  piloto real (Gate S4.2/S4.3) ser concluido do lado `mktplace`.
- **Atualizacao 2026-07-20 (Gate S4.2, provisionamento da credencial
  dedicada)**: com autorizacao explicita, o `mktplace` criou a role
  `gold_shopee_window_writer` (privilegios minimos: `SELECT` na Silver
  Shopee, `SELECT/INSERT/DELETE` só em `gold.marketplace_region_daily`,
  sem `UPDATE`/`TRUNCATE`/`CREATE`/superuser), criou o secret local
  `.env.gold-window-write.local` (gitignored, fora do Git) e rodou **somente**
  o preflight de leitura contra essa credencial nova — aprovado
  (`ok=True`, sem bloqueios). **Nenhuma linha de dado foi escrita, nenhum
  refresh/restore/sync foi executado.** Esta automacao externa **continua
  sem autorizacao para habilitar `--refresh-shopee-window`** — falta o
  Gate S4.3 (piloto real de escrita), que depende ainda de uma janela
  Shopee candidata (hoje nenhuma existe) e de autorizacao explicita
  separada.
- **Atualizacao 2026-07-20 (Gate S4.3a, modo de validacao sem persistencia)**:
  o `mktplace` implementou `--validate-shopee-window-write-path` — exercita
  o MESMO caminho de escrita do refresh real (secret dedicado, preflight,
  locks, staging temporaria, validacoes estruturais, reconciliacao Gold x
  fonte) mas **nunca** publica backup, nunca faz `DELETE`/`INSERT` na Gold
  e sempre termina em `ROLLBACK`, mesmo quando a janela esta divergente.
  So codigo, testes (34 novos, suite completa com 1338 passando) e
  documentacao — **nenhuma execucao real contra o banco nesta rodada**.
  Isso nao e o piloto real do Gate S4.3 (que ainda vai escrever de
  verdade); e um jeito seguro de confirmar que o caminho funciona antes
  dele. Continua valendo: esta automacao externa **nao deve habilitar**
  nenhum comando de escrita real (`--refresh-shopee-window`) ainda.

## Alertas e falhas comuns

### Sessao Shopee expirada

Sintoma: download cai na tela de login.

Acao:

```bat
rodar_shopee_login.bat
```

Depois rodar novamente:

```bat
rodar_shopee.bat
```

### Dry-run Raw falhou

Sintoma: lote reprovado antes de escrever no banco.

Acao:

- conferir se os tres arquivos foram baixados;
- conferir se o arquivo nao esta vazio;
- conferir headers;
- rodar `validate` manual para ver a saida.

### Silver falhou com validacao pre-insert

Sintoma: Raw entrou, mas Silver abortou.

Isso geralmente significa que a Shopee mudou dominio/header/formato.

Acao:

- identificar a mensagem primaria do Postgres;
- comparar o campo com o SQL/mapping do `mktplace`;
- preferir corrigir padronizacao do lote ou contrato versionado, nao burlar a
  validacao.

### Checkout mktplace divergente

Sintoma: `preflight` acusa commit diferente.

Acao:

- nao rodar carga sem revisar;
- voltar o checkout `.mktplace` ao commit esperado ou atualizar
  conscientemente o commit esperado no wrapper/handoff.

## Cuidados de seguranca

- Nao comitar `.mktplace/`.
- Nao comitar `.env`, `.env.shopee-write.local` ou
  `.env.shopee-silver-write.local`.
- Nao colar senhas do banco em chat, issue ou README.
- Como uma senha administrativa foi compartilhada durante a preparacao, e
  recomendavel rotaciona-la depois que a operacao estiver estabilizada.
- Usar a role dedicada da automacao para cargas recorrentes, nao usuario admin.

## Estado recomendado para o proximo colaborador

Antes de mexer, rodar:

```powershell
cd C:\Users\User\Documents\relatorio_mercos\shopee_automacao
.\.mktplace\.venv\Scripts\python.exe shopee_datamart.py preflight
.\.mktplace\.venv\Scripts\python.exe shopee_datamart.py inspect
.\.mktplace\.venv\Scripts\python.exe -m pytest -q
```

Se tudo passar, o ponto seguro de continuacao e:

1. parametrizar a marca no pipeline;
2. separar sessao/login por marca;
3. testar uma nova marca primeiro em `validate` sem escrita;
4. executar Raw/Silver para uma marca por vez;
5. so depois planejar Gold/Neon.

## Conclusao

A automacao Shopee esta integrada ao Data Mart para `kokeshi` ate Silver.

Ela baixa os relatorios, padroniza o lote, valida com o loader oficial do
`mktplace`, insere em Raw e transforma para Silver com reconciliacao final.

O proximo trabalho nao e mais descobrir a estrutura do banco nem criar o
primeiro insert. Isso ja foi feito. O proximo trabalho e evoluir com seguranca:
multi-marca primeiro, Gold/Neon depois.
