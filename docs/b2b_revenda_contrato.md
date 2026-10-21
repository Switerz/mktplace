# B2B / Revendedores — contrato de leitura da Torre B2B

**Gate:** TORRE-B2B-OCTAPRICE · **Estado:** código atrás de flags
**desligadas** em produção; nenhuma variável configurada no Render ou na Vercel.

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
navegador ──► nossa FastAPI (/api/v1/b2b-revenda) ──► Data API do Supabase da Torre B2B
                                   (chave só no backend)        (6 RPCs, allowlist fixa)
```

O navegador **nunca** fala com a Torre B2B e nunca recebe a chave.

## Regras de produto

| Regra | Onde está garantida |
|---|---|
| Piso = PMA do OctaPrice; não se mistura com o PDV de Lojas próprias | rota e schema separados; `Escopo.aviso_piso` |
| Amazon fora dos KPIs (a dedup da fonte descarta vendedores do mesmo anúncio) | allowlist `B2B_REVENDA_CANAIS_KPI`; sem filtro, a API envia a allowlist, **nunca NULL**; allowlist vazia ⇒ `unavailable` |
| Cobertura de identificação explícita | `cobertura_identificacao` (só contagens) |
| Frescor visível | `Frescor` em toda resposta (dia do relatório, situação, horário da consulta) |
| Fonte indisponível nunca vira zero | `status: "unavailable"` com `motivo_tecnico`; dia sem relatório não chama RPC |
| Cache de 15–30 min | `B2B_REVENDA_CACHE_TTL_SECONDS` (900–1800, padrão 1200); falha não é cacheada |
| Sem dado de cliente enquanto a Torre de Marketplaces não tiver autenticação | nenhuma rota devolve cliente, vendedor, seller, CNPJ, e-mail ou telefone; nenhuma aceita filtro por cliente/vendedor (testes de contrato) |

## Endpoints (todos agregados, só GET)

| Rota | Conteúdo |
|---|---|
| `GET /api/v1/b2b-revenda/frescor` | dia mais recente, dias disponíveis, situação, canais fora dos KPIs |
| `GET /api/v1/b2b-revenda/resumo` | anúncios, violação, sellers violando, desvio médio, buy box, severidade, cobertura de identificação |
| `GET /api/v1/b2b-revenda/canais-marcas` | marca × canal |
| `GET /api/v1/b2b-revenda/skus?limite=` | ranking de SKUs com violação (1–50) |

Filtros (parâmetros repetidos): `data`, `marcas` (`AP`/`BB`/`KS`), `canais`
(só os da allowlist), `severidades` (`grave`/`media`/`leve`), `skus`, `ufs`.
Recorte inválido ⇒ 422 com mensagem constante (o valor recebido não volta).
Com a flag desligada, as quatro rotas respondem 200 com
`status: "unavailable"` / `feature_flag_desligada` sem tocar a rede, como as
demais flags da API; a rota web responde 404.

Frescor: depois de 13h BRT o relatório do dia já é esperado (o import do
e-mail roda 08h40/10h40/12h40 BRT); antes disso, o de ontem ainda conta como
em dia. Os horários de coleta e de importação **não** saem das RPCs: a tela
diz isso em vez de mostrar um horário inventado.

## O que é lido na fonte — allowlist fixa

| RPC | Colunas pedidas (`select=`) | Parâmetros enviados (por nome) |
|---|---|---|
| `fn_monit_datas` | `data_ref` | — |
| `fn_monit_resumo` | `data_ref, total, violando, pct, sellers_violando, produtos, desvio_medio` | `p_data, p_sevs, p_marcas, p_canais, p_skus, p_ufs` |
| `fn_monit_severidade` | `leve, media, grave, com_buybox, viol_com_buybox` | `p_data, p_marcas, p_canais, p_skus, p_ufs` |
| `fn_monit_marca_canal` | `marca_id, canal, anuncios, violando, desvio_medio, preco_medio` | idem `resumo` |
| `fn_monit_top_produtos` | `sku, marca_id, exemplo, anuncios, violando, pct, desvio_medio` | idem + `p_limit` |
| `fn_monit_sellers` | **só** `identificado, violando` — contados e descartados no backend | idem + `p_limit` |

- Nenhum nome de RPC vem da requisição; não existe endpoint genérico.
- `p_vendedores` e `p_cnpjs` **nunca** são enviados.
- Parâmetros vão por nome no corpo JSON: se a fonte renomear um parâmetro ou
  coluna, a leitura falha alto (`contrato_da_fonte_quebrado`), não desloca valores.
- `fn_monit_sellers` e `fn_monit_marca_canal` são paginadas (`Range`) para não
  depender do `max-rows` da Data API.
- Nenhuma tabela, view, storage, auth ou outra RPC é consultada.

## Credencial

Chave da Data API do Supabase da Torre B2B, **só no backend**:

- vai no header `apikey` (o gateway sintetiza o `Authorization`);
- é `SecretStr` nas settings: não aparece em `repr`, log, resposta nem traceback;
- erro da fonte é classificado só pelo status HTTP e pelo campo `code` —
  mensagem, detalhe e hint do corpo não são lidos;
- o frontend tem teste que falha se qualquer variável da integração for citada.

A escolha da chave segue a regra **menos privilegiada que execute as seis
RPCs**. Qual chave foi usada e o resultado da sondagem ficam registrados fora
do repositório. O caminho de longo prazo continua sendo uma credencial
dedicada, pedida ao dono da Torre B2B.

## Variáveis de ambiente

API (Render) — nenhuma configurada nesta rodada:

| Variável | Padrão | Papel |
|---|---|---|
| `B2B_REVENDA_ENABLED` | `false` | liga os endpoints; desligado ⇒ `unavailable` sem rede |
| `B2B_REVENDA_SUPABASE_URL` | vazio | raiz do projeto (`https://<ref>.supabase.co`); só HTTPS, sem caminho |
| `B2B_REVENDA_SUPABASE_KEY` | vazio | chave da Data API (segredo) |
| `B2B_REVENDA_TIMEOUT_SECONDS` | `10` | 1–30 (conexão limitada a 5 s) |
| `B2B_REVENDA_CACHE_TTL_SECONDS` | `1200` | 900–1800 |
| `B2B_REVENDA_CANAIS_KPI` | `Mercadolivre,Shopee` | allowlist dos KPIs, com a grafia da fonte |

Web (Vercel, em build):

| Variável | Valores |
|---|---|
| `NEXT_PUBLIC_B2B_REVENDA_MODE` | ausente = desligada (404 e sem seletor) · `fixture` = dados fictícios com faixa de aviso, sem requisição · `api` = lê a nossa API |

## Ativação (gate próprio, fora deste PR)

1. Credencial definida e colocada direto na variável do Render (nunca em chat,
   e-mail ou commit).
2. Autenticação da Torre de Marketplaces definida antes de qualquer rota com
   dado de cliente (seller, cliente, vendedor, reincidência por seller).
3. `B2B_REVENDA_ENABLED=true` e reconciliação de `/frescor`, `/resumo`,
   `/canais-marcas` e `/skus` contra a fonte no mesmo dia e recorte.
4. Só então `NEXT_PUBLIC_B2B_REVENDA_MODE=api`.

## Limitações conhecidas

- Violação, desvio e PMA vêm prontos do OctaPrice; a Torre não aplica tolerância.
- Seller é identificado pelo nome normalizado; nomes iguais em canais diferentes contam como um.
- Cliente B2B depende da identificação manual feita na Torre B2B.
- Histórico limitado à retenção da fonte (~90 dias).
- Horários de coleta e importação não expostos pelas RPCs.
- `% violação` por SKU é recalculado aqui com 1 casa (a RPC arredonda a 0); as contagens são as da fonte.
