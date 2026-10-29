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

## Publicação (ordem)

1. **Migration no Neon** — manual, como as demais:

   ```bash
   cd apps/api
   alembic upgrade 032
   ```

   Reversível com `alembic downgrade 031` (derruba só as duas tabelas; nenhuma outra relação é tocada).

2. **Permissão de escrita** para o papel da `DATABASE_URL` do Render. São as
   primeiras tabelas que a API **escreve**. Trocar `<papel_da_api>` pelo papel real:

   ```sql
   GRANT SELECT, INSERT, UPDATE, DELETE ON marts.meta_gmv_mensal TO <papel_da_api>;
   GRANT SELECT, INSERT ON marts.meta_gmv_mensal_historico TO <papel_da_api>;
   GRANT USAGE ON SEQUENCE marts.meta_gmv_mensal_historico_id_seq TO <papel_da_api>;
   ```

   Se o papel da API for o dono do schema, o GRANT é desnecessário.

3. **Backend no Render** — republicar manualmente.
4. **Frontend** — a Vercel publica sozinha após o merge.

## Smoke pós-deploy

```bash
curl -s https://mktplace-api.onrender.com/api/v1/metas/2026-10
```

Esperado: `200` com `celulas: []` num mês sem metas. `503` com "Nao foi possivel
acessar as metas no banco agora." indica migration não aplicada ou falta de GRANT.

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
