# `pma_refresh` — runbook operacional

Gate PMA-OPS-2, 2026-09-25 — preparação. **Ativado no PMA-GO-LIVE-1, no mesmo
dia**: as duas flags estão ligadas, a publicação manual rodou com sucesso nos
três canais e a tarefa `mktplace_pma_refresh` está registrada para 07:30.
O estado corrente está na seção 5.

---

## 1. O que estava travado, e o que mudou

A fotografia do monitoramento de preços envelhece porque **ninguém a publica de
forma recorrente** — não porque a origem esteja atrasada. Medido em 25/09:

| fonte | último dado na origem | fotografia servida | defasagem |
|---|---|---|---:|
| `silver.stg_ml_item_price_history` | 25/09 06:03 (879 itens) | 21/09 | 4 dias |
| `raw.shopee_products` | 25/09 09:01 | 23/09 | 2 dias |
| `gold.tiktok_product_catalog` | 23/09 | 22/09 | 1 dia |

Eram **dois** bloqueios, e a ordem entre eles importa:

1. **`pma_refresh` não tem agendamento.** A única tarefa registrada no Task
   Scheduler é `mktplace_full_daily` (diária, 06:00), e `full_daily` não contém
   nenhum step do PMA. O publisher do ML rodou uma única vez em 20 dias.
2. **Shopee e TikTok eram recusados dentro do `pma_refresh`.**
   `channel_offer_publisher` passava `channel_enabled=False` **literal**, e os
   steps do orquestrador não mandam `--operator-override`. Registro em
   `audit.source_sync_run`:

   ```
   2026-09-22 21:22  channel_offer_snapshot  failed  recusado: channel_flag_disabled
   ```

Agendar antes de resolver (2) teria produzido uma falha diária previsível em
dois dos três canais. **Este gate resolveu (2) e deixou (1) para a decisão de
negócio.**

---

## 2. A autorização agora é configuração

Uma variável **por canal**, ambas **nascendo desligadas**:

| canal | variável |
|---|---|
| Shopee | `PMA_PUBLISH_SHOPEE_ENABLED` |
| TikTok | `PMA_PUBLISH_TIKTOK_ENABLED` |

Não existe variável global: ligar a Shopee não é decidir sobre o TikTok.

**Três comportamentos, e nenhum deles publica por acidente:**

| valor | efeito |
|---|---|
| ausente | **desligado** — é o default |
| `1` `true` `yes` `on` (qualquer caixa) | ligado |
| `0` `false` `no` `off` `""` | desligado |
| qualquer outra coisa | **levanta `PublishFlagError`** |

O terceiro caso merece explicação, porque a alternativa parece mais segura e não
é. Tratar `"treu"` como desligado também não publica — mas não publica **em
silêncio**, e quem digitou errado continuaria vendo a fotografia envelhecer sem
nenhuma pista. Exigir reconhecimento integral, e falhar alto no que não for
reconhecido, é o que torna a recusa legível. A mensagem nomeia a variável e
nunca ecoa o valor recebido.

`--operator-override` **continua existindo** e continua vencendo a flag: é o
caminho do humano para uma publicação pontual. O que mudou é que deixou de ser o
único caminho.

### O que a flag NÃO faz

Ligar a flag autoriza a *intenção* de publicar. As demais recusas de
`plan_publication` continuam de pé e continuam protegendo a fotografia anterior:

- `source_unavailable` — publicar zero linhas apagaria o que já está lá;
- `no_account_ran` — nenhuma conta executou: é desconhecimento, não observação;
- `empty_healthy` — a fonte respondeu vazia; preservar o antigo o faria passar
  por atual;
- `older_than_published` — reprocessar um dia velho não rebaixa o publicado.

---

## 3. Diagnóstico — descobrir **sem** tentar publicar

Antes, saber por que um canal não publicava exigia rodar o apply e ler a recusa
na auditoria: era preciso tentar publicar para descobrir que não daria.

```powershell
# O .env NAO e' carregado por este modulo — quem o carrega e'
# `pipelines.ops.orchestrate`. Medido no PMA-GO-LIVE-1: invocado direto, o
# comando morre com `falha de origem (KeyError)`, que e'
# `os.environ["DATAMART_DATABASE_URL"]` ausente. Injete o ambiente antes.
$env:PMA_PUBLISH_SHOPEE_ENABLED = 'true'
python -c "from dotenv import load_dotenv; load_dotenv('.env'); import runpy; runpy.run_module('pipelines.channel_offer_publisher', run_name='__main__')" -- --marketplace shopee
```

Sem `--apply` o comando é um ensaio real: lê a fonte por conexão **read-only**,
monta a candidata, roda as guardas de PII e de unicidade de chave — e **não**
adquire o lock, **não** abre auditoria e **não** escreve. A saída agora inclui o
portão de publicação:

```
  portao: RECUSARIA (channel_flag_disabled) | PMA_PUBLISH_SHOPEE_ENABLED=disabled override=False
  portao: nao avaliado aqui (exige o destino): source_available, accounts_that_ran,
          record_count, snapshot_older_than_published
```

A última linha não é enfeite: o relatório declara o próprio alcance. Sem ela,
`PUBLICARIA` pareceria garantia de publicação, e ele responde apenas pelo portão
da flag — as guardas que precisam da conexão de destino não foram avaliadas.

---

## 4. Comando oficial

O veículo **já existe e é o mesmo que o `full_daily` agendado usa**. Nenhuma
infraestrutura foi inventada nesta rodada.

```powershell
powershell.exe -NoProfile -NonInteractive `
  -File "C:\Users\Notebook\Desktop\mktplace\scripts\run_task.ps1" -TaskKey pma_refresh
```

O que esse wrapper entrega, e por isso não deve ser contornado:

- **lock lógico compartilhado com `full_daily`.** Deliberado: os publishers do
  PMA leem as mesmas fontes do Data Mart que o `full_daily` carrega. Importa
  mais aqui do que nos outros porque o sync do ML usa `pg_advisory_xact_lock`,
  que **espera** em vez de desistir — sem o lock lógico, uma execução
  concorrente ficaria pendurada até o timeout do step em vez de sair limpa;
- timeout de 3600 s e log por execução;
- exit code com política estrita (`PIPELINES_COM_EXIT_ESTRITO`).

Os publishers de canal têm, **além disso**, `pg_try_advisory_lock(917120017)`,
fail-fast: mesmo que o lock lógico fosse contornado, a segunda execução sairia
com exit 3 sem ler nem escrever. São duas camadas com propósitos distintos — a
lógica evita a disputa, a do Postgres garante a exclusão.

### Dependências

| dependência | por quê |
|---|---|
| `DATABASE_URL` (Neon, gravável) | destino e auditoria |
| `DATAMART_DATABASE_URL` (read-only) | fonte — **exige VPN** |
| migration `017` aplicada | `assert_apply_authorized` recusa antes do lock |
| `PMA_PUBLISH_*_ENABLED` | só para Shopee e TikTok; o ML não tem flag |

O step `pma_ml` **não** depende de flag: o Mercado Livre já está publicado e o
`sync_ml_listing_price_serving` nunca teve o bloqueio.

---

## 5. Estado corrente — ATIVO desde 2026-09-25

Executado no PMA-GO-LIVE-1, com autorização de Mário. Nada abaixo é plano: é o
que rodou.

### Flags

| variável | antes | depois |
|---|---|---|
| `PMA_PUBLISH_SHOPEE_ENABLED` | ausente (= off) | `true` (escopo **User**) |
| `PMA_PUBLISH_TIKTOK_ENABLED` | ausente (= off) | `true` (escopo **User**) |

Escopo **User**, não Machine, porque é o usuário `Notebook` que a Scheduled Task
usa. `Machine` permanece ausente de propósito: uma variável de máquina ligaria a
publicação para qualquer conta do host.

### Publicação manual

Comando — o veículo já existente, e nada mais:

```powershell
powershell.exe -NoProfile -NonInteractive `
  -File "C:\Users\Notebook\Desktop\mktplace\scripts\run_task.ps1" -TaskKey pma_refresh
```

`STATUS=SUCCESS EXITCODE=0`, 63 s. Em `audit.source_sync_run`:

| run | fonte | canal | linhas | status |
|---:|---|---|---:|---|
| 375 | `ml_listing_price_snapshot` | ML | 2.635 | success |
| 376 | `channel_offer_snapshot` | Shopee | 695 | success |
| 377 | `channel_offer_snapshot` | TikTok | 1.221 | success |

Nenhum run ficou em `running`; o advisory lock `917120017` voltou a `LIVRE`.

O `health_check` do mesmo ciclo saiu `FAILED`, e isso **não é do PMA**: ele mede
o frescor de TODAS as fontes da Torre e acusou 6 itens alheios (51 h a 1.228 h de
atraso, mais duas falhas de outros pipelines). Os cinco checks do PMA saíram
`[OK]`. O próprio orquestrador imprime "NÃO reexecute os publishers por causa da
saúde global" — o exit code do pipeline é decidido pelos canais, não pelo
diagnóstico.

### Resultado

Os três canais saíram de atrasados para `lag=0 fresh`:

| canal | observação | monitorados | comparáveis | sem referência |
|---|---|---:|---:|---:|
| ML | 2026-09-24 | 877 | 160 | 596 |
| Shopee | 2026-09-25 | 695 | 152 | 440 |
| TikTok | 2026-09-25 | 1.221 | 104 | 815 |

O ML fica em D-1 por contrato (`closed_day`); Shopee e TikTok em D0
(`snapshot_current`). As três partições fecham nos três canais.

### Agendamento

| campo | valor |
|---|---|
| nome | `mktplace_pma_refresh` |
| gatilho | diário, **07:30** |
| usuário | `Notebook` (Interactive, Limited) — o mesmo do `full_daily` |
| sobreposição | `MultipleInstances=IgnoreNew` |
| limite de execução | 1 h |
| diretório | `C:\Users\Notebook\Desktop\mktplace` |
| estado | `Ready`, habilitada |

**07:30 e não 06:00**: o `full_daily` começa às 06:00 e os dois compartilham o
lock lógico. Duração medida do `full_daily`: 1 a 10,1 min — 90 min de folga.
A tarefa **não foi disparada** para testar; a primeira execução agendada é a de
amanhã.

### Como desligar

Parar de publicar não exige deploy. Em ordem de reversibilidade:

```powershell
# 1. desabilitar a tarefa (para o agendamento, preserva os dados publicados)
Disable-ScheduledTask -TaskName 'mktplace_pma_refresh'

# 2. desligar os canais (a tarefa roda e RECUSA, deixando rastro na auditoria)
[Environment]::SetEnvironmentVariable('PMA_PUBLISH_SHOPEE_ENABLED', $null, 'User')
[Environment]::SetEnvironmentVariable('PMA_PUBLISH_TIKTOK_ENABLED', $null, 'User')
```

A opção 2 é a mais honesta quando se quer *saber* que a publicação foi barrada:
a recusa fica gravada em `audit.source_sync_run` como
`recusado: channel_flag_disabled`, enquanto a tarefa desabilitada não deixa
rastro nenhum. O ML não tem flag e continuaria publicando pela tarefa — para
pará-lo também, use a opção 1.

## 6. O que continua indisponível

- **A API não enxerga a ingestão bruta.** Ela lê apenas `marts.*` no Neon; o
  estado atual do Data Mart não é visível para o serving. É por isso que a tela
  declara esse relógio como *não observável aqui* em vez de omiti-lo — a omissão
  é exatamente o que leva alguém a concluir "a origem rodou, logo a tela está
  atual".
- **Sem VPN não há publicação de canal.** A fonte é o Data Mart.
- **A tarefa roda numa estação, não num servidor.** `mktplace_pma_refresh` vive
  no Task Scheduler do host do Mário, sob o usuário `Notebook`. Máquina
  desligada às 07:30 significa dia sem publicação — `StartWhenAvailable` faz o
  disparo atrasado acontecer ao ligar, mas não cobre um dia inteiro fora do ar.
- **Ninguém é avisado quando o refresh não roda.** A tela mostra a defasagem a
  quem a abre, e o `health_check` já roda dentro do ciclo — falta a superfície
  que avisa sem alguém olhar. O alerta na Torre continua pendente.
- **O `health_check` global está vermelho por dívida alheia ao PMA**: 6 fontes
  da Torre atrasadas ou falhando, sendo duas críticas. Não bloqueia a
  publicação de preços e não se resolve aqui.
