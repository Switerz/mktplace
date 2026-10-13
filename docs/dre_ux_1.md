# DRE-UX-1 — tela `/dre`

Data: 2026-10-13. Contrato consumido: `GET /api/v1/dre` (`docs/dre_mvp_1.md`, `docs/dre_contrato_0.md`).
Escopo: só frontend. Nenhuma fórmula, publisher, migration, banco ou regra financeira do backend mudou.

## O que a tela mostra

| Bloco | Fonte | Regra |
|---|---|---|
| Resumo | `total` | Receita, margem de contribuição conhecida, % e classificação com completude. Margem ausente = "Não calculável" + motivo |
| Comparação por canal | `por_canal` | Cada canal com a própria classificação: Shopee não calculável não esconde ML e TikTok |
| DRE vertical | recorte escolhido (total, canal ou marca) | 8 linhas do contrato + margem. `null` mostra status e motivo; afiliado recuado, sem "−", "já incluído em Taxas" |
| Comparação entre marcas | `por_marca` | Share = receita da marca ÷ receita do `total`, com valores crus. Ranking só entre comparáveis |
| Matriz marca × canal | `por_canal_marca` | Margem % por célula; "Sem venda" quando a célula não existe |

Atalho: com o total não calculável e algum canal calculável, a tela oferece "Ver só <canais calculáveis>", que só troca
o filtro — o agregado vem calculado pela API, nunca somado na tela.

## Filtros e URL

`ref_month` (YYYY-MM), `channels`, `brands`. Uma única função (`dreQuery`) escreve a URL e monta a requisição.
Sem `ref_month` a API escolhe a competência madura e a tela mostra qual; o padrão não é gravado na URL, para o link
seguir a competência madura quando ela avançar. `date_from`/`date_to` de outras telas são ignorados (não são mês
inteiro e dariam 422). `/dre` não entra em `FILTER_AWARE_PAGES`.

## Comparabilidade (ranking de margem)

Fora do ranking, sempre com motivo: `NAO_CALCULAVEL`; margem ausente; sem taxas ou sem CMV na conta; conjunto de
custos diferente do mais frequente entre os elegíveis (ex.: marca só no TikTok, sem Ads, contra marcas com Ads).

## Estados

carregando · sucesso completo · sucesso parcial · fotografia ausente (`nao_publicado`) · fotografia vencida
(`vencido`, `parcialmente_vencido`) · tabela ausente (`tabela_ausente`) · seleção sem dados · 422 (mensagem do
servidor + "Limpar filtros") · 503 · 404 ("API publicada ainda não serve a DRE") · rede. Sem modo demonstração.

## Dependência de deploy

Em 13/10/2026 a API de produção (`mktplace-api.onrender.com`) responde `/health` 200 e `/api/v1/dre` **404**: o
Render não tem a DRE. Até o deploy, a tela em produção mostra o estado "A API publicada ainda não serve a DRE".

## Leitura com dado real (API local → Neon, só leitura, jul/2026)

Fotografia `ok`; TikTok e ML `PARCIAL`; Shopee `NAO_CALCULAVEL`; total e **todas as marcas** `NAO_CALCULAVEL` com
a Shopee no filtro (a pior célula manda). A margem por marca só aparece na matriz (TikTok/ML) ou filtrando
`channels=tiktok,ml`. Por isso o ranking de marcas fica vazio na visão padrão — comportamento correto, não defeito.

## Validação

- `tests/dre.test.ts` (31 testes) com fixtures sintéticas em `tests/dre-fixtures.ts`; mutações (afiliado somável,
  share arredondado, não calculável no ranking, ausência como zero) derrubam testes.
- Suíte web, `tsc --noEmit`, `next build`.
- Navegador (Playwright): fixtures em 1440/1024/390 × claro/escuro + todos os estados; API local lendo o Neon em
  3 URLs × 3 larguras × 2 temas. Sem overflow horizontal, zero erro de console, requisição = URL.
