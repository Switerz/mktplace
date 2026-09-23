# Coletor de snapshot da Avoe — runbook operacional (Gate AVH-5B)

**O que este documento cobre:** como capturar um snapshot da Avoe Hub de forma
auditável e somente-leitura, e como validá-lo antes de qualquer publicação.

**O que ele não cobre:** publicação. Publicar no Neon é `snapshot_import --apply`
e depende de **autorização separada**, que este gate não concede e não antecipa.

---

## 1. Por que existe

O coletor do AVH-3A nunca foi versionado e não existe mais. Uma captura que não
se consegue repetir não é fonte: é lembrança. O `snapshot_import` do AVH-4A lê
arquivo e nunca fala com a Avoe — de propósito —, mas alguém precisa produzir
esse arquivo, e até aqui esse alguém era um script perdido.

Este gate fecha a ponta de cima: a captura passa a ser código versionado, com
teste, com hash e com manifesto.

| Papel | Módulo | Fala com a Avoe? | Fala com o Neon? |
|---|---|---|---|
| Sessão de navegador | `pipelines/avoe/snapshot_collect_session.py` | sim, só GET | não |
| Coletor | `pipelines/avoe/snapshot_collect.py` | sim, só GET | não |
| Contrato | `pipelines/avoe/snapshot_contract.py` | **não** | não |
| Importador | `pipelines/avoe/snapshot_import.py` | **não** | sim, com `--apply` |

O contrato e o importador **não foram tocados** por este gate.

---

## 2. Credencial: onde ela está, e onde ela não está

**A credencial nunca entra no repositório, nem em memória do Python.**

Quem digita usuário e senha é a pessoa, na tela do navegador. O driver de
sessão não preenche campo, não lê campo, e não tem por onde receber credencial:
não lê ambiente, não lê arquivo de configuração, não tem prompt, e a CLI aceita
exatamente quatro argumentos — `--url`, `--out-dir`, `--login-timeout-seconds`
e `--page-size`. Há teste que falha se aparecer um quinto.

A leitura dos dados acontece **dentro da página**. O JavaScript monta a
requisição com os globais que a própria aplicação já tem em memória e devolve
ao Python só três campos:

```
{ status, rows, content_range }
```

Nenhum cabeçalho de autenticação atravessa essa fronteira. Se um dia
atravessar, a página inteira é recusada — `ENVELOPE_FIELDS` é uma allowlist
fechada, e o teste `test_envelope_com_campo_extra_e_recusado` prova que o
**nome** do campo aparece na mensagem de erro e o **valor** não.

O contexto do navegador é efêmero: sem `user_data_dir`, sem `storage_state`,
sem perfil em disco. Ao fim, `signOut()` e o contexto é destruído.

### Uma única tentativa de login

Não é disciplina de quem opera, é código. Cada **passo** de autenticação
declarado em `--auth-path` passa uma vez antes do armamento; repetir o mesmo
passo é segunda tentativa e fica bloqueado. Se houver MFA, CAPTCHA, erro de
senha ou tenant errado, a captura para e espera intervenção humana.

**O login pode ter mais de um passo, e isso importa na prática.** A Avoe
confere a credencial numa Edge Function (`/functions/v1/login-usuario`, que usa
service role e nunca expõe nada ao navegador) e só depois troca por sessão em
`/auth/v1/`. São dois POSTs distintos, uma tentativa só.

Uma rota de autenticação **não declarada é bloqueada como qualquer escrita** —
foi o que aconteceu na primeira execução deste gate, e o login simplesmente não
passou. É o comportamento correto: o guard não adivinha o que é autenticação.
Descubra os passos observando a rede uma vez e declare-os explicitamente.

---

## 3. Somente leitura

`ReadOnlyGuard` intercepta toda a rede do contexto:

- **antes do armamento** — uma requisição de autenticação passa; POST, PUT,
  PATCH e DELETE para qualquer outro destino já são abortados;
- **depois do armamento** (imediatamente após a sessão autenticar) — nada
  mutável passa, **sem exceção por destino ou origem**, nem a própria
  autenticação.

O registro do guard guarda só método e host. Nunca a query, nunca o corpo.
A contagem de bloqueios viaja para o manifesto, como evidência.

---

## 4. Como a fonte é lida

Não há export nativo para as duas tabelas de interesse. Os botões "Exportar
Excel" da interface cobrem SKU, estoque, FIFO, curva ABC e kits — nenhum cobre
`resumo_marca_mes` nem `faturamento_diario_marca`. Por isso a leitura é por
requisição GET observada, e não por download de arquivo.

- método: `GET` — e só ele existe no JavaScript do coletor;
- paginação: `limit` / `offset`, ordenada por `id` (âncora estável);
- tamanho de página: 1000, que é o teto do servidor;
- contagem: `Prefer: count=exact`, lida do `Content-Range`.

### O que faz a coleta parar

Tudo abaixo aborta a captura **sem gravar arquivo nenhum**:

| Situação | Como é detectada |
|---|---|
| HTTP 401 / 403 / 429 / 5xx | status fora de `{200, 206}` |
| Página repetida | conjunto de `id` igual ao de uma página anterior |
| Página ausente | linhas lidas ≠ total do `Content-Range` |
| Contagem mudou no meio | total do `Content-Range` diverge entre páginas |
| Resposta truncada | mais linhas do que o `limit` pedido |
| `Content-Range` ausente ou malformado | sem total não se prova completude |
| Coluna obrigatória ausente | schema do contrato AVH-4A |
| Coluna nova na origem | schema do contrato AVH-4A |
| Coluna proibida | schema do contrato AVH-4A |
| Duplicata na chave de origem | mesma regra do importador |
| `id` ausente ou repetido | a paginação por offset perde a âncora |
| Timeout / página fechada | exceção do navegador, sem repassar a mensagem |
| Destino dentro de um repositório git | `assert_fora_do_repositorio` |
| **Zero linha** | leitura sem permissão, ver abaixo |

Não há degradação. Um snapshot parcial não é snapshot.

### Zero linha é recusa, não captura vazia

Com RLS, o PostgREST responde `200` com `[]` e total `0` tanto para "a tabela
está vazia" quanto para "esta sessão não enxerga nada". As duas tabelas têm
milhares de linhas e nunca esvaziam, então só a segunda leitura é plausível.

Isso não é hipótese: foi o desfecho real da primeira tentativa deste gate. A
sonda de prontidão aceitou a tela de login como sessão pronta, a fonte
respondeu sem erro e sem dado, e a captura terminou com **exit 0, manifesto
válido e dois arquivos vazios**. Um snapshot vazio de aparência válida é pior
que uma falha, porque atravessa a validação seguinte sem tropeçar.

Duas travas cobrem isso agora: a sonda exige a sessão que de fato autoriza a
leitura, e o coletor recusa qualquer tabela com zero linha.

**Coluna nova é parada, não aviso.** Se a Avoe adicionar um campo, a captura
falha nomeando a coluna e o contrato do AVH-4A precisa ser revisto antes que
qualquer dado novo entre. É o mesmo princípio do importador: a origem mudar de
forma é decisão de contrato, não de carga.

---

## 5. Determinismo

Duas capturas do mesmo conteúdo produzem **byte a byte** os mesmos arquivos:

- linhas ordenadas pela chave de origem, com `id` como desempate;
- chaves de cada objeto ordenadas;
- separadores compactos, sem espaço supérfluo;
- `\n` explícito — nunca CRLF, mesmo capturando no Windows;
- UTF-8 sem BOM.

A ordem em que a fonte devolve as páginas não muda o arquivo. O tamanho de
página também não: só o campo `pages` do manifesto muda, porque ele descreve a
leitura, não o dado.

---

## 6. Como rodar

### Pré-requisitos

```bash
pip install playwright
python -m playwright install chromium
```

### A captura

```bash
python -m pipelines.avoe.snapshot_collect_session \
  --url <URL DA TELA DE LOGIN> \
  --out-dir <DIRETORIO FORA DO REPOSITORIO> \
  --auth-path <PASSO 1 DO LOGIN> \
  --auth-path <PASSO 2 DO LOGIN>
```

A URL e as rotas de autenticação **não são versionadas** — o repositório não
conhece o endereço da Avoe, e isso é intencional. Peça a quem opera.

Sem `--auth-path`, só a rota padrão do Supabase (`/auth/v1/`) é reconhecida
como autenticação, e um login que passe por outro lugar será bloqueado.

O diretório de saída tem de ficar fora de qualquer repositório git. O coletor
recusa o contrário: dado de terceiro não é versionável, e é assim que um
`git add` acidental acontece.

O navegador abre. **Você digita a credencial na tela.** O processo espera,
arma o guard quando a sessão autenticar, coleta, faz logout e descarta tudo.

### A validação

```bash
python -m pipelines.avoe.snapshot_import --snapshot-dir <DIRETORIO>
```

**Sem `--apply`.** A última linha do relatório precisa dizer:

```
ESCRITA: nenhuma (sem --apply). Zero linha gravada.
```

Publicar é outro gate, com outra autorização.

---

## 7. O que a captura produz

```
MANIFEST.json            manifesto, com hash e contagem por arquivo
MANIFEST.sha256          hash do manifesto
resumo_marca_mes.jsonl   metas mensais por marca
faturamento_diario_marca.jsonl   faturamento diário por marca e plataforma
```

O manifesto declara, além do que o contrato exige:

- `collector.gate` e `collector.version` — de onde veio a captura;
- `collector.read_method` — como foi lida;
- `collector.blocked_mutating_requests` — quantas mutações o guard abortou;
- `collector.credentials_in_artifact: "none"`;
- por tabela: `declared_total`, `pages` e `columns` observadas.

`columns` é o registro de qual schema a fonte tinha naquele instante. É o que
permite provar, depois, que uma divergência veio da origem e não da leitura.

---

## 8. Sanitização de log

O coletor imprime tabelas, contagens, páginas, hashes e tempos. **Nunca uma
linha de dado.** O resumo não expõe marca, valor, meta nem o caminho completo
do diretório do operador — só o nome da pasta.

Há teste que falha se um valor de linha aparecer no resumo.

---

## 9. Limites conhecidos

**A captura é uma fotografia sem relógio da fonte.** A Avoe não declara
`updated_at` de tabela nem versão de schema. `captured_at` é o relógio de quem
capturou, não o da origem. Se alguém escrever na Avoe durante a coleta, o
`Content-Range` muda e a captura falha — mas uma escrita entre a última página
e o `captured_at` é invisível.

**O guard não protege o que acontece antes dele.** Entre o login e o
armamento há uma janela de milissegundos. A aplicação não escreve ao carregar,
mas isso é observação, não garantia contratual.

**Token não renova depois do armamento.** O refresh do Supabase é um POST para
`/auth/v1/` e é bloqueado. Uma coleta que passe da validade da sessão recebe
401 e para — corretamente, mas sem recuperação automática. A coleta leva
segundos; a sessão, muito mais.

**Nem toda conta da Avoe consegue ler.** A aplicação tem dois regimes: o login
próprio dela, que devolve um token de sessão de 12h, e a conta espelhada no
Supabase Auth, que existe só para quem foi migrado. As consultas às tabelas
usam o JWT do Auth **se houver** e caem na chave pública quando não há — e a
chave pública não enxerga as duas tabelas. Uma conta sem espelho no Auth entra
na tela normalmente e lê zero linha.

Isso é pendência de permissão na origem, não de código, e o coletor para com
essa mensagem em vez de produzir arquivo.

**Uma captura não substitui ingestão.** Continua valendo o que o
`docs/avoe_snapshot_manual_runbook.md` §2 diz: isto é ponte, não arquitetura.
O dado da Avoe é digitado à mão, não medido, e nunca soma ao GMV oficial da
Torre.

---

## 10. Testes

`pipelines/tests/test_avoe_snapshot_collect.py` — 115 testes, sem rede, sem
navegador e sem banco.

Cobrem paginação completa, última página parcial, página exatamente cheia,
página repetida, página ausente, **zero linha**, mudança de contagem, resposta
truncada, `Content-Range` ausente e malformado, teto de páginas, coluna
obrigatória ausente, coluna nova, coluna proibida, mudança de schema entre
páginas, duplicata de chave nas duas tabelas, `id` ausente e repetido, os
status 400/401/403/404/429/500/502/503, as três falhas sintéticas do leitor,
timeout, bloqueio de requisição mutável, login de dois passos com uma tentativa
cada, rota de autenticação não declarada, os três estados da sonda de sessão,
determinismo por ordem de resposta e por tamanho de página, reprodutibilidade
de manifesto e hashes, recusa de gravar dentro de repositório git, e a
integração de ponta a ponta com o contrato do AVH-4A.

Três blocos varrem o **código executável** — sem docstrings e sem comentários,
via AST — e provam ausência de senha, usuário fixo, token, cookie, apikey,
bearer, `storage_state`, leitura de ambiente, escrita na Avoe e qualquer
referência a Neon ou Data Mart.

A varredura mede o que executa, não o que está escrito em prosa. Varrer a
docstring encontraria a palavra `senha` na frase que promete que não há senha
nenhuma — e o efeito prático seria ensinar a não documentar.
