# Metas mensais de GMV — runbook (METAS-1)

Aba **Metas** (`/metas`): o time digita a meta de GMV de cada marca em cada
canal, mês a mês. Os totais por marca, por canal e global são soma, calculada
na tela.

## O que existe

| Peça | Onde |
|---|---|
| Tabelas | `marts.meta_gmv_mensal` (grão `mes × brand × channel`) e `marts.meta_gmv_mensal_historico` — migration `032` (sobre a `031` do TikTok Ads) |
| API | `GET /api/v1/metas/{AAAA-MM}` e `PUT /api/v1/metas/{AAAA-MM}` (`apps/api/app/routers/metas.py`) |
| Tela | `apps/web/app/metas/`, grade em `apps/web/src/components/metas/MetasGrade.tsx`, regras em `apps/web/src/lib/metas.ts` |

Regras:

- **Campo vazio = sem meta**, diferente de meta zero. Célula sem meta não tem linha no banco.
- **Sem login**, por decisão do proprietário: qualquer pessoa com o link edita.
  A proteção é validação fechada (5 marcas, 3 canais, valor de 0 a 9.999.999.999,99,
  mês de 2024-01 a 2030-12), limite de 30 gravações/min por cliente e 120/min no
  total, e o **histórico** de toda alteração (valor anterior, valor novo, horário).
- O GMV realizado aparece embaixo de cada campo como referência, lido de
  `/api/v1/performance/brands` até hoje. Se essa leitura falhar, a grade continua editável.

## Publicação (ordem segura)

A migration vem **antes** do código: as tabelas novas não são lidas pelo backend
atual, então criá-las não muda nada em produção. Só depois o código que as usa entra.

1. **Checar o Neon antes.** Em `apps/api`, com a `DATABASE_URL` do Render no
   ambiente (ou no `.env`, que é ignorado pelo git):

   ```bash
   python tools/metas_deploy_check.py antes
   ```

   Exige `alembic_version = 031` (a do TikTok Ads). Se estiver abaixo, **pare**:
   `alembic upgrade 032` aplicaria também migrations de outras frentes.

2. **Migration 032:**

   ```bash
   alembic upgrade 032
   ```

   Reversível com `alembic downgrade 031`, que derruba só as duas tabelas e não toca
   nenhuma outra relação.

3. **Checar depois.** Confere tabelas, privilégios do papel da API e uma escrita de
   teste desfeita por ROLLBACK (nada fica gravado):

   ```bash
   python tools/metas_deploy_check.py depois
   ```

   Se um privilégio falhar, o papel que roda a migration não é o mesmo da API.
   Nesse caso, conceda o acesso e rode o passo 3 de novo (troque `<papel_da_api>`
   pelo papel real):

   ```sql
   GRANT SELECT, INSERT, UPDATE, DELETE ON marts.meta_gmv_mensal TO <papel_da_api>;
   GRANT SELECT, INSERT ON marts.meta_gmv_mensal_historico TO <papel_da_api>;
   GRANT USAGE ON SEQUENCE marts.meta_gmv_mensal_historico_id_seq TO <papel_da_api>;
   ```

4. **Mesclar o PR da aba Metas** na `main`. A Vercel publica o frontend sozinha.
5. **Republicar o backend no Render** logo em seguida. Até isso acontecer, a aba
   Metas responde 404 e mostra erro; as outras telas não mudam.
6. **Smoke** (abaixo).
7. **Mesclar o PR da Gerencial** (reapontado para a `main`), aguardar a Vercel e
   abrir a Gerencial.

## Smoke pós-deploy

```bash
curl -s https://mktplace-api.onrender.com/api/v1/metas/2026-10
```

- `200` com `celulas: []` num mês sem metas: backend e banco certos.
- `404`: o Render ainda não publicou o código novo.
- `503` com "Nao foi possivel acessar as metas no banco agora.": falta migration ou privilégio (volte ao passo 3).

Depois abra `/metas` no domínio canônico, salve uma meta, recarregue a página e
confira que o valor voltou. Na Gerencial, com "Mês atual", o bloco principal deve
mostrar a meta.

## Desfazer uma alteração

```sql
SELECT mes, brand, channel, meta_anterior, meta_nova, alterado_em
  FROM marts.meta_gmv_mensal_historico
 WHERE mes = DATE '2026-10-01'
 ORDER BY alterado_em DESC;
```

Digite o `meta_anterior` de volta na tela (vazio quando era `NULL`) e salve.

## Testes

- API: `apps/api/tests/test_metas.py` (sem banco) e
  `apps/api/tests/test_metas_integracao.py` (SQL real; só roda com
  `METAS_TEST_DATABASE_URL` apontando para um Postgres **descartável** — o teste
  recria o schema `marts` nele; nunca aponte para o Neon).
- Web: `apps/web/tests/metas.test.ts`.

## Fora deste escopo

- Usar as metas na Gerencial: entregue em seguida (GERENCIAL-METAS-1, seção abaixo).
- `apps/web/src/lib/goals-data.ts` (metas antigas fixas no código, sem uso em nenhuma tela) ficou intocado.

## Gerencial contra a meta (GERENCIAL-METAS-1)

A Visão Gerencial lê `GET /api/v1/metas/{AAAA-MM}` quando o período filtrado é
um mês só, começando no dia 1, e vai até o fim do mês ou até hoje (preset "Mês
atual"). Nesse caso o bloco principal mostra:

- **atingimento**: GMV realizado ÷ meta do escopo (canais e marcas filtrados);
- **projeção** pelo ritmo: realizado ÷ dias com dado × dias do mês. "Dias com dado"
  é o último dia com venda na série diária, porque o Data Mart chega com D-1;
- **quanto falta por dia** nos dias restantes;
- **selo**: `Meta atingida` (realizado ≥ meta), `No caminho` (projeção ≥ meta),
  `Atenção` (projeção entre 95% e 100%), `Abaixo do ritmo` (< 95%) e
  `Meta não atingida` (mês encerrado abaixo da meta).

Fora disso (um período que não é um mês, ou um mês sem meta) o bloco mostra o GMV
contra o período anterior e aponta para a aba Metas. Ele nunca inventa uma meta.
As regras estão em `apps/web/src/lib/gerencial/meta-progress.ts`.

## Meta do dia e curva (METAS-DIARIAS-1)

Origem: planilha "Meta diária Marketplace — nov/26 (validação)" (abas Resumo,
Curva, Meta diária, Realizado e Acompanhamento).

- **A meta do dia não é digitada.** Ela sai da meta mensal da aba Metas:
  `meta do dia = meta do mês × peso do dia ÷ soma dos pesos do mês`. Mudou a
  mensal, a diária muda junto.
- **Curva** = um peso por dia, por canal (ML e Shopee), igual para todas as marcas
  do canal. Peso 1,00 = dia normal médio; 11/11 e Black Friday pesam 2,45 (ML) e
  3,5 (Shopee). Fica fixa no código, em `apps/web/src/lib/metas-curva.ts`
  (`CURVAS`), copiada da aba Curva. **Só novembro/26 tem curva.** Mês ou canal sem
  curva (TikTok, dezembro em diante) divide a meta igualmente pelos dias, que é a
  conta linear de antes. Editar a curva pelo app ficou para depois.
- **Aba Metas:** abaixo da grade, a tabela "Meta por dia" abre a meta do mês dia a
  dia (total, acumulado, % do mês, por canal e por marca). Ela acompanha o que
  está na grade, inclusive alteração ainda não salva ("prévia, não salva").
- **Gerencial em "Hoje"** (ou qualquer período de um dia só): o bloco principal
  compara com a **meta do dia**. Cada canal chega num horário (em 30/10/26 só a
  Shopee da Kokeshi entrava durante o dia; o resto chegava no dia seguinte), então:
  - dia em andamento: marca × canal sem venda fica **fora da conta** e aparece em
    "Sem venda ainda"; o selo é `Dia em andamento`, sem cor de status;
  - dia fechado: se o canal chegou para alguma marca, marca sem venda conta zero;
    se não chegou para nenhuma, as células dele ficam fora. Selo `Meta do dia
    batida` ou `Meta do dia não batida`.
  - Exceção: o dia 1 sozinho também é o "Mês atual" do dia 2 (o preset termina em
    D-1); aí a meta continua mensal.
- **Gerencial em "Mês atual"** com curva: a projeção passa a ser
  `realizado ÷ fatia do mês esperada pela curva até o último dia com dado`, e a
  barra ganha a marca "plano até DD/MM" (meta acumulada pela curva, como a aba
  Acompanhamento). Sem curva, a fatia é `dias com dado ÷ dias do mês` e nada muda.
- **Realizado comparado = só marca × canal com meta.** Antes, a Gerencial somava o
  GMV de todos os canais filtrados contra a meta. Em outubro/26 o TikTok (sem meta)
  vendeu R$ 12 mi e o atingimento de "Mês atual" saía 186% em vez de 85%. O GMV do
  período (à esquerda) continua com todos os canais; o chip "TikTok Shop fora da
  meta" avisa quando um canal filtrado não tem meta. Em "Marcas", a barra de uma
  marca com meta soma só os canais que têm meta.

Diferença de base conhecida: a planilha usa "receita sem brindes" (base do dash de
mix) e a Kokeshi Shopee pelos relatórios do Seller Center; o app usa o GMV do Data
Mart, com a Kokeshi Shopee pela API. Os números podem não bater ao centavo.

Testes: `apps/web/tests/metas-curva.test.ts` (valores da planilha) e
`apps/web/tests/gerencial-metas.test.ts`.
