# `pma_refresh` — runbook operacional

Gate PMA-OPS-2, 2026-09-25. Estado desta rodada: **o caminho está preparado e
inerte**. Nenhuma flag foi ligada, nenhuma tarefa foi agendada, nenhuma
publicação foi executada.

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

```bash
python -m pipelines.channel_offer_publisher --marketplace shopee
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

## 5. O ponto exato em que o agendamento deve entrar

**Nada abaixo foi executado.** É o que falta, na ordem.

1. **Ligar a flag por canal**, no ambiente onde a tarefa roda — não no `.env` do
   repositório. Confirmar com o `--diagnose` da seção 3: a linha do portão deve
   passar de `RECUSARIA (channel_flag_disabled)` para `PUBLICARIA`.
2. **Uma execução manual completa**, pelo comando da seção 4, e conferir em
   `audit.source_sync_run` que os três canais saíram `success` com
   `rows_loaded > 0`.
3. **Registrar a tarefa** no Task Scheduler:

   ```powershell
   # NAO EXECUTADO nesta rodada — é o passo seguinte.
   $acao = New-ScheduledTaskAction -Execute "powershell.exe" `
     -Argument '-NoProfile -NonInteractive -File "C:\Users\Notebook\Desktop\mktplace\scripts\run_task.ps1" -TaskKey pma_refresh'
   $gatilho = New-ScheduledTaskTrigger -Daily -At 07:30
   Register-ScheduledTask -TaskName "mktplace_pma_refresh" -Action $acao -Trigger $gatilho
   ```

   **07:30, e não 06:00.** O `full_daily` começa às 06:00 e os dois compartilham
   o lock lógico: disparar junto faria o `pma_refresh` sair `BLOCKED` todo dia.
   A folga precisa cobrir a duração real do `full_daily` — medi-la antes de
   fixar o horário.

   **Depois do `full_daily`, não dentro dele.** As duas razões pelas quais a
   Shopee saiu do `full_daily` no Gate C1 valem aqui: um canal lento não deve
   derrubar o pipeline dos outros, e o `pma_refresh` já tem política de exit
   própria.

4. **Alerta de atraso dentro da Torre.** A matéria-prima já existe: o step
   `health_check` é `always_run` dentro do `pma_refresh`, e a tela agora expõe
   os relógios separados (captura na origem, publicação, defasagem) mais a
   declaração explícita de que a ingestão bruta **não é observável** pela API.
   Falta a superfície que avisa sem alguém abrir a tela.

---

## 6. O que continua indisponível

- **A API não enxerga a ingestão bruta.** Ela lê apenas `marts.*` no Neon; o
  estado atual do Data Mart não é visível para o serving. É por isso que a tela
  declara esse relógio como *não observável aqui* em vez de omiti-lo — a omissão
  é exatamente o que leva alguém a concluir "a origem rodou, logo a tela está
  atual".
- **Sem VPN não há publicação de canal.** A fonte é o Data Mart.
- **O agendamento não existe.** Até o passo 3 acima, toda publicação é manual.
