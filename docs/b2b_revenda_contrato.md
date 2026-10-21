# B2B / Revendedores — contrato de leitura da Torre B2B

**Gate:** TORRE-B2B-OCTAPRICE · **Estado:** código pronto atrás de flags
**desligadas**; credencial dedicada **não provisionada**; nenhuma conexão com a
Torre B2B foi feita a partir deste repositório.

## O que é

Nova visão do Monitoramento de Preços: `Lojas próprias | B2B / Revendedores`.
Mostra se revendedores B2B anunciam abaixo do **PMA informado pelo OctaPrice**,
em que canal, marca e SKU, com que gravidade e de quando é o dado.

A Torre B2B é **fonte externa e somente leitura**. Ela já recebe o relatório
diário do OctaPrice e expõe as RPCs `fn_monit_*`; nós chamamos as **mesmas**
RPCs que alimentam a tela de lá, para os números baterem por construção.
Nada é escrito do lado de lá, e nada no projeto da Torre B2B é alterado por
este repositório.

```
navegador ──► nossa FastAPI (/api/v1/b2b-revenda) ──► Torre B2B (role dedicada, read-only)
```

O navegador **nunca** fala com a Torre B2B.

## Regras de produto

| Regra | Onde está garantida |
|---|---|
| Piso = PMA do OctaPrice; não se mistura com o PDV de Lojas próprias | rota e schema separados; `Escopo.aviso_piso` |
| Amazon fora dos KPIs (a dedup da fonte descarta vendedores do mesmo anúncio) | allowlist `B2B_REVENDA_CANAIS_KPI`; sem filtro, a API envia a allowlist, **nunca NULL**; allowlist vazia ⇒ `unavailable` |
| Cobertura de identificação explícita | `cobertura_identificacao` (contagens agregadas no banco) |
| Coleta, importação e frescor visíveis | `Frescor` em toda resposta |
| Fonte indisponível nunca vira zero | `status: "unavailable"` com `motivo_tecnico`; dia sem relatório não chama RPC |
| Cache de 15–30 min | `B2B_REVENDA_CACHE_TTL_SECONDS` (900–1800, padrão 1200); falha não é cacheada |
| Sem dado de cliente enquanto a Torre de Marketplaces não tiver autenticação | nenhuma rota devolve cliente, vendedor, seller, CNPJ, e-mail ou telefone; nenhuma aceita filtro por cliente/vendedor (testes de contrato) |

## Endpoints (todos agregados)

| Rota | Conteúdo |
|---|---|
| `GET /api/v1/b2b-revenda/frescor` | carga mais recente, coleta, importação, situação, dias disponíveis, canais fora dos KPIs |
| `GET /api/v1/b2b-revenda/resumo` | anúncios, violação, sellers violando, desvio médio, buy box, severidade, cobertura de identificação |
| `GET /api/v1/b2b-revenda/canais-marcas` | marca × canal |
| `GET /api/v1/b2b-revenda/skus?limite=` | ranking de SKUs com violação (1–50) |

Filtros (parâmetros repetidos): `data`, `marcas` (`AP`/`BB`/`KS`), `canais`
(só os da allowlist), `severidades` (`grave`/`media`/`leve`), `skus`, `ufs`.
Recorte inválido ⇒ 422 com mensagem constante (o valor recebido não volta).

Frescor: depois de 13h BRT o relatório do dia já é esperado (o import do
e-mail roda 08h40/10h40/12h40 BRT); antes disso, o de ontem ainda conta como
em dia. `dias_de_atraso` é a diferença para o dia esperado.

## Objetos lidos na fonte

| Objeto | Uso | Parâmetros enviados |
|---|---|---|
| `fn_monit_datas()` | dias com relatório | — |
| `fn_monit_resumo(...)` | KPIs | `p_data, p_sevs, p_marcas, p_canais, p_skus, p_ufs` |
| `fn_monit_severidade(...)` | leve/média/grave + buy box | `p_data, p_marcas, p_canais, p_skus, p_ufs` |
| `fn_monit_marca_canal(...)` | marca × canal | idem `resumo` |
| `fn_monit_top_produtos(...)` | SKUs | idem + `p_limit` |
| `fn_monit_sellers(...)` | **só contagens**, agregadas no SQL | idem + `p_limit` |
| `fato_monitoramento_preco` | só `data_ref, data_coleta, created_at, canal` da carga mais recente | — |

Chamadas por **nome de parâmetro** (`p_x => ...`): se a fonte renomear um
parâmetro, a leitura falha alto (`contrato_da_fonte_quebrado`) em vez de
deslocar valores. `p_vendedores` e `p_cnpjs` **nunca** são enviados.

Campos consumidos por RPC: `resumo` → `data_ref, total, violando, pct,
sellers_violando, produtos, desvio_medio`; `severidade` → `leve, media, grave,
com_buybox, viol_com_buybox`; `marca_canal` → `marca_id, canal, anuncios,
violando, desvio_medio, preco_medio`; `top_produtos` → `sku, marca_id,
exemplo, anuncios, violando, pct, desvio_medio`; `sellers` → só
`identificado` e `violando`, dentro de `count/sum`.

## Credencial: role dedicada, somente leitura

Pedida ao dono da Torre B2B. **Nunca** usar chave de aplicação da Torre B2B.

```sql
CREATE ROLE leitor_torre_mktplace LOGIN PASSWORD '<gerada pelo dono>'
  NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS
  CONNECTION LIMIT 4;
ALTER ROLE leitor_torre_mktplace SET default_transaction_read_only = on;
ALTER ROLE leitor_torre_mktplace SET statement_timeout = '15s';
GRANT CONNECT ON DATABASE <banco> TO leitor_torre_mktplace;
GRANT USAGE ON SCHEMA public TO leitor_torre_mktplace;
GRANT EXECUTE ON FUNCTION
  public.fn_monit_datas(),
  public.fn_monit_resumo(date,text[],text[],text[],text[],text[],text[],text[]),
  public.fn_monit_severidade(date,text[],text[],text[],text[],text[],text[]),
  public.fn_monit_marca_canal(date,text[],text[],text[],text[],text[],text[],text[]),
  public.fn_monit_top_produtos(date,int,text[],text[],text[],text[],text[],text[],text[]),
  public.fn_monit_sellers(date,int,text[],text[],text[],text[],text[],text[],text[])
TO leitor_torre_mktplace;
-- As RPCs sao SECURITY INVOKER e leem `fp.*`: exigem SELECT na fato inteira
-- (que nao tem e-mail nem telefone).
GRANT SELECT ON public.fato_monitoramento_preco TO leitor_torre_mktplace;
-- Do de-para, SO' as quatro colunas que as RPCs referenciam. Sem e-mail,
-- telefone, razao social, cidade ou observacoes.
GRANT SELECT (seller_norm, cliente_nome, cnpj, vendedor)
  ON public.monitor_seller_cliente TO leitor_torre_mktplace;
```

Sem `INSERT`, `UPDATE`, `DELETE`, `CREATE` nem acesso a e-mail e telefone.
As conexões chegam com `application_name = torre-mktplace-b2b-revenda`.

**Validado localmente** (Postgres 17 descartável com a DDL e as RPCs da
fonte, dados sintéticos e exatamente esta role, com `EXECUTE` de `PUBLIC`
revogado): o adapter real roda; os números batem com a RPC chamada direto; a
role não lê `email`/`telefone`, não lê o de-para inteiro, não executa RPC
fora da lista, não escreve e não cria objeto.

## Variáveis de ambiente

API (Render):

| Variável | Padrão | Papel |
|---|---|---|
| `B2B_REVENDA_ENABLED` | `false` | liga os endpoints; desligado ⇒ `unavailable` sem conexão |
| `B2B_REVENDA_DATABASE_URL` | vazio | DSN da role dedicada (`postgresql://...`); vazio ⇒ `credencial_ausente` |
| `B2B_REVENDA_SSLMODE` | `require` | `require` / `verify-ca` / `verify-full` (`disable` só em banco local) |
| `B2B_REVENDA_CONNECT_TIMEOUT_SECONDS` | `5` | 1–30 |
| `B2B_REVENDA_STATEMENT_TIMEOUT_MS` | `10000` | 1000–30000, aplicado por transação |
| `B2B_REVENDA_CACHE_TTL_SECONDS` | `1200` | 900–1800 |
| `B2B_REVENDA_CANAIS_KPI` | `Mercadolivre,Shopee` | allowlist dos KPIs, com a grafia da fonte |

Web (Vercel, em build):

| Variável | Valores |
|---|---|
| `NEXT_PUBLIC_B2B_REVENDA_MODE` | ausente = desligada (404 e sem seletor) · `fixture` = dados fictícios com faixa de aviso, sem requisição · `api` = lê a nossa API |

## Ativação (fora deste PR)

1. Dono da Torre B2B provisiona a role e entrega a DSN por canal seguro,
   direto na variável do Render (nunca em chat, e-mail ou commit).
2. Definir a autenticação da Torre de Marketplaces antes de qualquer rota com
   dado de cliente (seller, cliente, vendedor, reincidência por seller).
3. Ligar `B2B_REVENDA_ENABLED` e conferir `/frescor` e `/resumo` contra a tela
   da Torre B2B no mesmo dia e recorte (sem Amazon nos dois lados).
4. Só então `NEXT_PUBLIC_B2B_REVENDA_MODE=api`.

## Limitações conhecidas

- Violação, desvio e PMA vêm prontos do OctaPrice; a Torre não aplica tolerância.
- Seller é identificado pelo nome normalizado; nomes iguais em canais diferentes contam como um.
- Cliente B2B depende da identificação manual feita na Torre B2B.
- Histórico limitado à retenção da fonte (~90 dias).
- `% violação` por SKU é recalculado aqui com 1 casa (a RPC arredonda a 0); as contagens são as da fonte.
- Horário da coleta exibido como a fonte o gravou.
