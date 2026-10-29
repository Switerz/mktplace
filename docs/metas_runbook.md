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

- Usar as metas na Gerencial (atingimento, projeção contra a meta) — próxima entrega.
- `apps/web/src/lib/goals-data.ts` (metas antigas fixas no código, sem uso em nenhuma tela) ficou intocado.
