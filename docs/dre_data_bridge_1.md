# DRE-DATA-BRIDGE-1 — insumos da DRE servíveis em produção

Data: 2026-10-13
Depende de: PR #66 (DRE-MVP-1). Contrato: `docs/dre_contrato_0.md`.
**Nada foi aplicado:** migration, publicação, deploy e merge aguardam autorização.

## 1. Problema e solução

A DRE precisa de CMV fiscal e repasse TikTok, que só existem no Data Mart, e o Render não alcança o Data Mart (Gate G4).
A solução separa leitura e serviço:

```
Data Mart (read-only) ──► pipelines.dre.insumo_publisher ──► Neon: marts.fact_dre_insumo_mensal ──► GET /api/v1/dre
      só no publisher            dry-run padrão, --apply             agregados mensais               só Neon
```

A requisição não abre conexão com o Data Mart. Um teste troca o engine do Data Mart por um objeto que explode em qualquer
uso, e outro confere que os módulos da rota não citam `datamart_engine`, `silver.` ou `gold.`.

## 2. Tabela `marts.fact_dre_insumo_mensal` (migration 025)

Head antes: `024`, linear, confirmado no código e no `alembic_version` do Neon (leitura). Sem ramificação.

| Coluna | Papel |
|---|---|
| `competencia`, `canal`, `marca`, `componente` | Chave. Competência sempre dia 1 (CHECK) |
| `valor` | Soma com o **sinal da origem**. `NULL` ⇔ `ausente_na_origem` (CHECK) |
| `sinal_origem` | Convenção de custo da fonte (1 ou −1); o serving recusa convenção diferente da esperada |
| `cobertura`, `provisorio_share` | Faixa [0, 1], sem `NaN` (CHECK) |
| `status_reconciliacao`, `motivo` | `reconciliado`, `nao_reconciliado`, `ausente_na_origem`, `nao_avaliado` |
| `data_max_origem` | Última emissão fiscal / última atualização do repasse |
| `publicado_em`, `lote_id` | Instante e lote (`audit.source_sync_run`, FK) |
| `procedencia` | Tabela de origem e versão da consulta |

Componentes (lista fechada por CHECK): `cmv`, `cmv_receita_fiscal`, `tiktok_taxa_repasse`, `tiktok_frete_repasse`,
`tiktok_afiliado_repasse`, `tiktok_receita_repasse`, `tiktok_residuo_identidade`, `tiktok_tx_afiliado_maior_taxa`,
`tiktok_transacoes`. Os três últimos são a **evidência de que o afiliado está dentro da taxa**: resíduo da identidade
do repasse e número de transações em que o afiliado excede a taxa (precisa ser zero).

Nenhum pedido, nota, SKU, settlement ou dado pessoal entra; um teste confere as colunas.

## 3. Publisher `pipelines.dre.insumo_publisher`

```
python -m pipelines.dre.insumo_publisher --de 2026-07 --ate 2026-07          # dry-run
python -m pipelines.dre.insumo_publisher --meses 4 --apply                     # 4 meses fechados
python -m pipelines.dre.insumo_publisher --meses 4 --apply --sem-vendas 2026-08:shopee   # mes/canal sem vendas, declarado
```

| Garantia | Como |
|---|---|
| Dry-run por padrão | Sem `--apply`: lê, monta, reconcilia, compara com o publicado; zero escrita e zero auditoria |
| Origem read-only | `SET TRANSACTION READ ONLY` + `statement_timeout` na conexão do Data Mart |
| Transação única no Neon | `engine.begin()`: lock, auditoria, `DELETE` do escopo, `INSERT`, fechamento do lote — tudo ou nada |
| Advisory lock | `pg_try_advisory_xact_lock(918140025)` antes de ler a fonte; ocupado → sai 75 sem escrever |
| Recusa antes da escrita | Chave repetida, CMV ≠ agregado mensal independente (`gold.nf_vendas_plataforma_mensal`), CMV negativo, cobertura/provisório fora de [0, 1], identidade do repasse violada, afiliado > taxa |
| Escopo nunca vazio em silêncio | Universo esperado vazio, ou par competência × canal sem nenhuma célula com receita na Torre → **recusa antes do `DELETE`** (sai 65, auditoria `failed`, fotografia anterior intacta byte a byte). Mês/canal legitimamente sem vendas só passa declarado com `--sem-vendas YYYY-MM:canal`, e a declaração é conferida: se houver receita, recusa |
| Argumentos validados | `--meses <= 0`, `--de` posterior a `--ate`, competência fora de `YYYY-MM`, canal desconhecido, `--canais` vazio e `--sem-vendas` malformado ou fora da janela → erro do argparse (sai 2) **antes de abrir qualquer conexão** |
| Ausência ≠ zero | Chave esperada sem dado na origem → linha `NULL` `ausente_na_origem` |
| Sem fotografia velha | A publicação substitui o escopo `competências × canais` inteiro; chave que sumiu é apagada |
| Idempotente | Mesma fonte → mesmo conteúdo; só `lote_id`/`publicado_em` mudam |
| Auditoria | `audit.source_sync_run` (`source_name = dre_insumo_mensal`); falha registrada em transação própria |
| Sem segredo em log | `sanitizar()` remove DSN, host, IP, usuário e senha |

Códigos de saída: `0` ok · `65` reconciliação recusada · `69` origem indisponível · `75` lock ocupado ·
`78` migration ausente · `1` inesperado.

**Estado do dado não é recusa:** CMV cuja receita fiscal não bate com o GMV (Shopee hoje) é publicado com
`nao_reconciliado`, e o serving o recusa.

## 4. Serving (falha fechada)

| Situação | Componente | Motivo exposto |
|---|---|---|
| Tabela inexistente (migration não aplicada) | `snapshot_ausente` | "migration 025 não aplicada" |
| Competência não publicada | `snapshot_ausente` | "competência não publicada" |
| Lote não `success` | `snapshot_ausente` | "lote de publicação não concluído" |
| Publicado há mais de 48 h | `snapshot_vencido` | instante da publicação e limite |
| `ausente_na_origem` | `indisponivel` | sem dado na origem |
| `nao_reconciliado` na publicação, ou razão com o GMV **atual** fora de 0,95–1,05 | `nao_reconciliado` | razão medida |
| Convenção de sinal publicada diferente da esperada | `sinal_inesperado` | — |

Cada componente publicado expõe `data_as_of` e `publicado_em`. No agregado, vale a data **mais antiga** entre as
células. O topo da resposta traz `fontes.insumos_dre` com `estado`, `motivo`, `publicado_em_mais_antigo`,
`publicado_em_mais_recente` e `publicado_em` — este último é a publicação **mais antiga** (referência conservadora):
um conjunto misto nunca parece mais novo do que sua parte mais velha.

**Período padrão da DRE:** sem período informado, a rota usa a **competência madura mais recente** (regra M+2), não o
mês anterior — que nunca é maduro. Em 13/10/2026 isso dá 01/07–31/07/2026, e `periodo.origem` vem como
`padrao_competencia_madura`. Período informado é sempre respeitado; mês imaturo informado sai `NAO_CALCULAVEL` com o
motivo. O padrão dos outros endpoints (mês anterior) não muda.

Regras do contrato preservadas: afiliado TikTok `somavel = false`; comissão do ML uma vez; sinal sem `abs()`;
"margem de contribuição conhecida"; Shopee `NAO_CALCULAVEL`.

## 5. Airflow

- O CLI é instalável pela wheel raiz: `pipelines.dre` entrou no `include`, e a wheel construída tem um único pacote de
  topo (`pipelines`), sem `tests`, `alembic` ou `app`.
- O publisher **não importa Alembic nem `app`**; um teste trava isso pelo AST.
- Compatível com o SQLAlchemy 1.4.54 da imagem: engines com `future=True`, e o driver é fixado em `psycopg2`
  quando a URL não declara outro. A suíte do publisher passa em 1.4.54 e em 2.0.
- Variáveis: `DATABASE_URL` (Neon, escrita) e `DATAMART_DATABASE_URL` (leitura).
- Sugestão de agenda: diária, `--meses 4`, depois da carga fiscal. O limite de 48 h tolera uma execução perdida.
- **Não criado:** DAG, Connection ou Environment. A migration é operação manual e separada.

**Contrato da DAG (PR separado, no repositório do Airflow):**
1. a DAG **só chama o publisher**: `python -m pipelines.dre.insumo_publisher --meses 4 --apply`, e trata o código de saída
   (`65`/`69`/`75`/`78` = falha visível, sem retry automático em `65` e `78`);
2. **nunca roda Alembic** e não importa o pacote `app`; a migration 025 é aplicada à mão, uma vez, fora do Airflow;
3. **não cria Environment** nem Connection nova: usa as variáveis já previstas (`DATABASE_URL`, `DATAMART_DATABASE_URL`);
4. **não toca o banco de metadados do Airflow**: o pacote instalado não traz `alembic`, `app` nem `tests` (verificado na
   wheel), e o publisher só fala com os dois DSNs acima;
5. `--sem-vendas` nunca entra fixo na DAG: é decisão de operador, caso a caso.

## 6. SQL offline da migration (revisado)

Gerado por `alembic upgrade 024:025 --sql`, com URL fictícia, sem conexão. O mesmo texto foi executado num PostgreSQL 16
descartável pelos testes de integração.

```sql
BEGIN;
-- Running upgrade 024 -> 025
CREATE TABLE marts.fact_dre_insumo_mensal (
        competencia           DATE        NOT NULL,
        canal                 TEXT        NOT NULL,
        marca                 TEXT        NOT NULL,
        componente            TEXT        NOT NULL,
        valor                 NUMERIC(18, 2),
        sinal_origem          SMALLINT,
        cobertura             NUMERIC(9, 6),
        provisorio_share      NUMERIC(9, 6),
        status_reconciliacao  TEXT        NOT NULL,
        motivo                TEXT,
        data_max_origem       TIMESTAMPTZ,
        publicado_em          TIMESTAMPTZ NOT NULL,
        lote_id               INT         NOT NULL
                              REFERENCES audit.source_sync_run (sync_run_id),
        procedencia           TEXT        NOT NULL,
        CONSTRAINT pk_fdim PRIMARY KEY (competencia, canal, marca, componente),
        CONSTRAINT ck_fdim_competencia_dia_1
            CHECK (competencia = date_trunc('month', competencia)::date),
        CONSTRAINT ck_fdim_canal CHECK (canal IN ('tiktok', 'ml', 'shopee')),
        CONSTRAINT ck_fdim_marca_nao_vazia CHECK (length(btrim(marca)) > 0),
        CONSTRAINT ck_fdim_componente CHECK (componente IN (
            'cmv', 'cmv_receita_fiscal', 'tiktok_taxa_repasse', 'tiktok_frete_repasse',
            'tiktok_afiliado_repasse', 'tiktok_receita_repasse', 'tiktok_residuo_identidade',
            'tiktok_tx_afiliado_maior_taxa', 'tiktok_transacoes')),
        CONSTRAINT ck_fdim_status CHECK (status_reconciliacao IN (
            'reconciliado', 'nao_reconciliado', 'ausente_na_origem', 'nao_avaliado')),
        CONSTRAINT ck_fdim_ausencia_explicita
            CHECK ((valor IS NULL) = (status_reconciliacao = 'ausente_na_origem')),
        CONSTRAINT ck_fdim_valor_nao_nan CHECK (valor IS NULL OR valor <> 'NaN'),
        CONSTRAINT ck_fdim_sinal CHECK (sinal_origem IS NULL OR sinal_origem IN (-1, 1)),
        CONSTRAINT ck_fdim_cobertura
            CHECK (cobertura IS NULL OR (cobertura <> 'NaN' AND cobertura >= 0 AND cobertura <= 1)),
        CONSTRAINT ck_fdim_provisorio
            CHECK (provisorio_share IS NULL
                   OR (provisorio_share <> 'NaN' AND provisorio_share >= 0 AND provisorio_share <= 1)),
        CONSTRAINT ck_fdim_procedencia_nao_vazia CHECK (length(btrim(procedencia)) > 0)
    );
CREATE INDEX ix_fdim_lote ON marts.fact_dre_insumo_mensal (lote_id);
-- (+ 4 COMMENT ON; ver o arquivo da migration)
UPDATE alembic_version SET version_num='025' WHERE alembic_version.version_num = '024';
COMMIT;
```

Downgrade: `DROP TABLE IF EXISTS marts.fact_dre_insumo_mensal;` e `version_num` volta a `024`.

Pontos revisados:
- só cria objetos novos; nada existente é alterado;
- a FK trava `audit.source_sync_run` por instantes, só no `CREATE`;
- `alembic_version` avança de `024` para `025` na mesma transação.

## 7. Validação executada

| Prova | Resultado |
|---|---|
| Dry-run real de jul/2026 (Neon + Data Mart, leitura) | 63 linhas, 0 recusas; 14 CMV (4 `nao_reconciliado` = Shopee); 5 marcas × 7 componentes do repasse; resíduo 0; 0 transações com afiliado > taxa |
| Serving novo alimentado por esse lote, contra o #66 | **0 diferenças** nas 14 células canal × marca (componentes, status, classificação, margem; tolerância R$ 0,50) |
| Latência da rota contra o Neon real, sem Data Mart | mediana ~1,1 s (1 ou 4 meses), contra 8–30 s no #66 |
| Testes do publisher (puros) | 51, em SQLAlchemy 2.0 e 1.4.54 |
| Integração em PostgreSQL 16 descartável | 21, em SQLAlchemy 2.0 e 1.4.54: dry-run sem escrita, idempotência, snapshot parcial, recusa, rollback integral, origem indisponível, lock ocupado, migration ausente, up/down/up, CHECKs, universo vazio, canal que some, `--sem-vendas` |
| Contraprova do universo vazio | no código anterior a esta revisão, julho apagado da Torre → `--apply` saía **0 (success)**; com a guarda desligada, a fotografia é apagada (teste executável); com a guarda, sai 65 e a fotografia fica igual byte a byte |
| Ponta a ponta (publica e serve) | 5: serve sem Data Mart, comissão ML uma vez, lote vencido, latência local |
| Serving (testes da API) | 86 |
| Mutações | publicar com recusa, não apagar escopo, ignorar lock, ignorar idade, ignorar status do lote, padrão voltar ao mês anterior, resumo pelo lote mais novo, `--meses` sem validação, guarda de escopo desligada: todas pegas |
| Suíte da API | mesmas 43 falhas da `main` (worktree sem `.env`), nenhuma nova |

Achados no caminho:
- `ZoneInfo` sem `tzdata` quebra no Windows → offset fixo −03:00;
- SQLAlchemy 2.1 troca o driver padrão → `+psycopg2` explícito;
- a primeira versão contava taxa positiva sem afiliado como violação, e o dry-run real a recusou → corrigido.

## 8. Plano de aplicação (requer autorização em cada passo)

**Regra: a DRE não vai para produção sem recorrência pronta.** Uma publicação manual vence em 48 h; servir a DRE sem a
DAG funcionando seria mostrar um número que caduca em dois dias.

1. Mesclar #66 e este PR. O merge **não** faz deploy (Render é manual). Se outro deploy acontecer antes do passo 6, a
   rota sobe sem tabela e responde `snapshot_ausente` — sem erro e sem número.
2. **Migration no Neon**, manual e fora do Airflow: `cd apps/api && alembic upgrade 025`. Conferir
   `SELECT version_num FROM alembic_version` = `025` e `to_regclass('marts.fact_dre_insumo_mensal')` não nulo.
3. **Dry-run** do publisher na estação com VPN: `python -m pipelines.dre.insumo_publisher --meses 4`. Exigir status `ok`
   e 0 recusas.
4. **Primeira publicação** manual: o mesmo comando com `--apply`. Conferir uma linha `success` em `audit.source_sync_run`
   e a contagem de linhas igual à do dry-run.
5. **Recorrência:** PR separado no repositório do Airflow com a DAG que só chama o publisher (contrato no §5). Ativar e
   observar **pelo menos uma execução agendada com `success`**.
6. Só então, **deploy do Render** (manual). Conferir em `/api/v1/dre` (sem período) que `periodo.origem =
   padrao_competencia_madura`, `fontes.insumos_dre.estado = ok`, e que ML e TikTok da competência saem `PARCIAL`.

## 9. Rollback

| Situação | Ação | Efeito |
|---|---|---|
| Publicação com valor errado | `DELETE FROM marts.fact_dre_insumo_mensal WHERE lote_id = <lote>` | Componentes voltam a `snapshot_ausente` |
| Desligar a ponte | `alembic downgrade 024` | Tabela some; serving responde `tabela_ausente`, sem erro |
| Reverter o serving | Reverter o merge deste PR | Volta o #66 (Data Mart na requisição) |
| Publisher agendado falhando | Nada a desfazer: a transação é única | O lote anterior vence em 48 h e a tela mostra "desatualizado" |

## 10. Riscos e decisões abertas

- **48 h de validade** é decisão a ratificar (D5): mais curto expõe falha de agenda mais cedo; mais longo tolera mais.
- **`updated_at` da silver sem fuso** é tratado como UTC (inferência).
- O publisher lê o repasse com a mesma varredura de ~3,8 GB (≈80 s medidos para um mês). Aceitável num job diário; um
  índice por `order_create_time` na silver ajudaria, mas é fora do nosso repositório.
- A reconciliação do CMV contra o GMV acontece duas vezes: na publicação e no serving, com o GMV atual. Uma mudança de
  GMV depois da publicação derruba o componente para `nao_reconciliado` até a próxima publicação. É intencional.
