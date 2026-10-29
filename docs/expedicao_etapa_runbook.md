# Etapa do pedido na Expedição — como ligar em produção

Mostra **em que etapa está cada pedido** da fila (ex.: "A preparar", "Aguardando
coleta", "Em separação"): na Jornada da Expedição e no popup de travados.

O código está todo na `main`:
- PR #125 (T-0015): pipeline, migration 033 e jornada por etapa;
- PR deste runbook: a API devolve a etapa de cada pedido, e a tela mostra.

Nada liga sozinho. São **4 passos**, todos seguros. A ordem não importa: o
publicador e a API detectam a coluna nova e, sem ela, seguem exatamente como
hoje.

## 1. Aprovar e mesclar o PR

A Vercel publica a tela sozinha a partir da `main`.

## 2. Banco (Neon): aplicar a migration 033

```bash
cd apps/api
alembic upgrade head   # 032 -> 033
```

- A migration acrescenta duas colunas a `marts.expedicao_fila_atual`: `etapa`,
  com default `nao_publicada` e CHECK de domínio, e `etapa_fonte`, com o status
  bruto da fonte para auditoria.
- No PostgreSQL 11+ isso **não reescreve a tabela**.
- Conferir: `alembic current` deve mostrar `033 (head)`.
- Para voltar: `alembic downgrade 032`. O publicador volta sozinho ao formato
  antigo.

## 3. Render (API): Manual Deploy do último commit da `main`

A API passa a devolver:
- a etapa de cada pedido em `GET /api/v1/expedicao` (campo `etapa`);
- as etapas reais em `GET /api/v1/expedicao/jornada`.

Nenhuma variável de ambiente nova.

## 4. Airflow (`goca-se/airflow`): usar a versão nova do pipeline

- Atualize a versão do `mktplace` usada pela DAG
  `marketplace_tower_expedicao_refresh` (pin ou imagem) para a `main` atual.
- A próxima execução (a cada 2 h) grava a etapa.

## Como conferir

Depois da próxima execução da DAG:

1. `GET https://mktplace-api.onrender.com/api/v1/expedicao/jornada?channel=shopee`:
   em `fila_aberta.etapas` aparecem "A preparar" e "Aguardando coleta", e não
   mais só "Aguardando expedição".
2. Na tela `/expedicao`:
   - a seção **Jornada** mostra a fila por etapa;
   - em **Travados → ver quais**, o popup mostra "Onde estão travados" e a
     coluna **Etapa**.

## Fora deste runbook: número do pedido

O número do pedido continua **fora da tela de propósito**: a API da Torre é
pública, sem login, e o número permite abrir o pedido no painel do
marketplace. Para mostrá-lo é preciso, antes, colocar login na Torre. Esse é um
projeto próprio.
