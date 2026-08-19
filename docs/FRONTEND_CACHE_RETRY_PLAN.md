# PF1 — Política de cache e retry do frontend

**Estado: `PF1 IMPLEMENTADO E VALIDADO LOCALMENTE — NÃO VERSIONADO.`** Task 1/2 (auditoria e desenho) e Task 2/2 (implementação e QA) concluídas. Aguardando integração e versionamento, depois de coordenar com o OM1 (§ 22).

Base inspecionada: **`e67594866486e252fe19777aec502d8be4f45bff`** (`origin/main` no
momento da auditoria, working tree limpo). A **Task 1/2** não alterou nenhuma linha de código. A **Task 2/2** alterou exatamente
três arquivos — `apps/web/src/lib/api-client.ts`,
`apps/web/tests/request-freshness.test.ts` e este documento — e **não** tocou
`package.json` nem `package-lock.json`. Nenhum commit, push ou deploy em nenhuma das
duas.

---

## 1. Objetivo

Fazer com que **falhas, respostas degradadas e fallbacks de demonstração nunca deixem o
botão "Tentar novamente" inerte**, com a menor correção segura possível.

Hoje o botão existe em 13 arquivos e, na maioria das telas, **não consulta a rede** se a
primeira tentativa falhou: o usuário clica, a tela pisca e repete a mesma
indisponibilidade por até cinco minutos. O que se quer preservar é igualmente
importante: o cache de respostas bem-sucedidas continua legítimo e continua valendo cinco
minutos, inclusive no retry composto da Gerencial, em que só a fonte que falhou precisa
voltar à rede.

Fora de escopo: retry automático, backoff, polling, deduplicação de chamadas
concorrentes, mudança de contrato de dados, de métrica, de fallback comercial ou de
estado visual.

---

## 2. Causa raiz

Três linhas de [`apps/web/src/lib/api-client.ts`](../apps/web/src/lib/api-client.ts),
L88-L93:

```ts
async function withCache<T>(key: string, fn: () => Promise<T>): Promise<T> {
  const hit = cacheGet<T>(key);
  if (hit !== undefined) return hit;
  const result = await fn();
  return cacheSet(key, result);   // <-- armazena QUALQUER coisa, inclusive falha
}
```

`cacheSet` é incondicional. `withCache` não tem nenhuma noção de sucesso: ele memoiza o
valor resolvido, seja ele uma resposta da API, `null`, ou um objeto de demonstração.

O segundo elo é a **sentinela de miss**. `cacheGet` devolve `undefined` para indicar
"não tem no cache", e `withCache` decide por `hit !== undefined`. Como
**`null !== undefined`**, um `null` armazenado é indistinguível de um acerto legítimo. A
escolha de `undefined` como sentinela está correta — é ela que permite cachear `0`,
`false`, `""` e `[]` (§ 3, Caso D) — mas ela **não** protege contra `null` armazenado.

O terceiro elo está em `apiFetch` (L97-L108): ele **captura todo erro e devolve `null`**.

```ts
async function apiFetch<T>(path: string): Promise<T | null> {
  try {
    const res = await fetch(`${API_URL}${path}`);
    if (!res.ok) return null;
    return (await res.json()) as T;
  } catch { return null; }
}
```

Consequência: **HTTP não-2xx, erro de rede, timeout e JSON inválido colapsam todos no
mesmo `null`** — e nenhum deles chega a `withCache` como exceção. Se `apiFetch`
rejeitasse, `withCache` não chamaria `cacheSet` e o defeito não existiria. É porque a
falha é um *valor de retorno normal* que ela entra no cache.

### 2.1 O elo que fecha o diagnóstico: `retryKey` nunca chega à chave do cache

Todas as telas com retry seguem o mesmo padrão:

```ts
const [retryKey, setRetryKey] = useState(0);
const requestKey = useMemo(() => buildRequestKey({ ...filtros, retryKey }), [...]);
useEffect(() => { fetchX(...) }, [...filtros, retryKey]);
// botão: onClick={() => { setError(null); setRetryKey((k) => k + 1); }}
```

`retryKey` entra na **identidade React da requisição** — e faz isso corretamente, porque
foi desenhado nos Gates U4/U5 para resolver frescor de resposta, não cache. Mas a
**chave do cache** é construída dentro de `api-client.ts` a partir de
`qs.toString()`, e **`retryKey` não participa dela**. O efeito roda de novo, chama
`fetchX` de novo, e `withCache` devolve o mesmo `null`/mock sem tocar a rede.

**As duas identidades são disjuntas, e é aí que o retry se perde.**

---

## 3. Prova do defeito

A prova é executável e roda **fora do repositório** (`%TEMP%`, `prova.mjs`), como manda
o escopo desta task: **nenhum teste versionado foi criado**. O mecanismo é transcrito
literalmente de `api-client.ts` L73-L108; a única diferença é um relógio injetável no
lugar de `Date.now()`, para provar a expiração do TTL sem esperar cinco minutos.

Sete casos, **0 asserções reprovadas**.

### Caso A — `null` é armazenado (padrão "cru": 12 das 21 funções)

```
1a chamada devolve null (rede consultada 1x)
o null FOI ARMAZENADO no _cache
o valor armazenado e' literalmente null
DEFEITO: o retry NAO consultou a rede (1 -> 1 chamadas)
```

### Caso B — o fallback de demonstração é armazenado (7 das 21 funções)

```
1a chamada devolve MOCK com live:false
o MOCK FOI ARMAZENADO no _cache
2a chamada devolve a MESMA referencia de objeto (veio do cache)
DEFEITO: o retry NAO consultou a rede (1 -> 1)
```

**Agravante que também é a solução:** o valor cacheado **não é indistinguível**. Ele
carrega `live: false`. A informação necessária para decidir "não cachear isto" já existe
dentro do próprio objeto (§ 8).

### Caso C — sucesso live dentro do TTL: comportamento correto, a preservar

```
1a chamada devolve live:true
2a chamada reutiliza o cache
CORRETO: 1 unica chamada de rede em 4 min
```

### Caso D — `0`, `false`, `[]` e `""` são valores válidos

```
0:        tratado como HIT valido, nao como miss
false:    tratado como HIT valido, nao como miss
[] vazio: tratado como HIT valido, nao como miss
'' vazio: tratado como HIT valido, nao como miss
```

`hit !== undefined` acerta aqui, e **qualquer correção precisa preservar exatamente esta
propriedade**. Trocar a sentinela por falsy (`if (hit)`) quebraria as quatro linhas
acima e transformaria zero legítimo em cache miss.

### Caso E — expiração do TTL

```
CORRETO: apos o TTL a fonte foi consultada de novo (1 -> 2)
cacheGet remove a entrada expirada antes de devolver undefined
DEFEITO: "Tentar novamente" segue inerte por 4min59s (1 -> 1)
so' depois de 5 min inteiros a rede volta a ser consultada
```

O TTL é a única saída hoje. O usuário não tem como saber disso, e o botão não comunica
"espere cinco minutos".

### Caso F — retry composto da Gerencial: 6 fontes, 1 falha

```
rodada 1: {overview:1, brands:1, trend:1, canais:1, quality:1, exec:1}
rodada 2: {overview:1, brands:1, trend:1, canais:1, quality:1, exec:2}

DEFEITO: a fonte que FALHOU (overview) NAO consultou a rede no retry
overview voltou do cache com o mock de novo
as 4 fontes com sucesso vieram do cache (CORRETO e desejavel)
fetchExecutiveSummary consultou a rede — e' a UNICA sem cache
```

Leitura direta: **hoje o retry da Gerencial só funciona para a única fonte que não tem
cache.** E as quatro fontes bem-sucedidas voltarem do cache é o comportamento
*desejado* — é exatamente o que a correção precisa manter.

### Caso G — deduplicação in-flight: **não existe**

```
NAO existe dedup in-flight: 2 chamadas de rede para 1 chave
as duas resolvem objetos distintos; a segunda sobrescreve o cache
```

`withCache` armazena o **valor resolvido**, não a promise. Duas chamadas concorrentes com
a mesma chave batem na rede duas vezes. **Não é o defeito do PF1 e não faz parte do
escopo mínimo** — fica registrado como observação de arquitetura.

---

## 4. Arquitetura atual

```
  botão "Tentar novamente"
      └─> setRetryKey(k => k + 1)
              └─> requestKey = buildRequestKey({ ...filtros, retryKey })   [identidade React]
              └─> useEffect([...filtros, retryKey])
                      └─> fetchX(selection, filters)
                              └─> qs = buildFilterQuery(...)               [identidade do CACHE]
                              └─> withCache(`nome:${qs}`, callback)
                                      ├─ HIT  ──> devolve valor armazenado  ◀── inclui null e mock
                                      └─ MISS ──> callback()
                                                    └─> apiFetch(path)
                                                            ├─ ok       ──> payload
                                                            └─ falha    ──> null
                                                    ├─ padrão cru:      devolve null
                                                    └─ padrão fallback: devolve {live:false, data:mock}
                                              └─> cacheSet(key, <qualquer coisa>)
```

- **`_cache`**: `Map<string, { data: unknown; at: number }>` em memória de módulo. Vive
  no cliente, por aba; perde-se em recarga. **Não há cache de servidor ou SSR**:
  `unstable_cache`, `revalidate`, `next/cache`, `fetchCache` e `force-cache` têm **zero
  ocorrências** no projeto — o que elimina um dos gatilhos de stop-loss (§ 15).
- **`CACHE_TTL`**: `5 * 60 * 1000`.
- **`cacheGet`**: expira e remove a entrada antes de devolver `undefined`.
- **`cacheSet`**: incondicional, devolve o próprio valor.
- **`withCache`**: nenhuma noção de sucesso.
- **Nada disso é exportado.** `api-client.ts` tem **zero exports** com "cache" no nome,
  o que é relevante para a estratégia de teste (§ 11).

### 4.1 Os dois padrões de call site

| padrão | nº de funções | retorno em sucesso | retorno em falha | o que é cacheado na falha |
|---|---|---|---|---|
| **cru** | 12 | payload da API | **`null`** | `null` |
| **com fallback** | 7 | `{ live: true, … }` | `{ live: false, …, mock/vazio }` | o objeto de demonstração |
| **envelope** | 2 | `{ data, live: raw != null }` | `{ data: null, live: false }` | o envelope com `live:false` |

> **Correção aplicada na Task 2/2 (ver § 18.2):** Inteligência e Operações estavam
> classificadas aqui como "cru", por engano. Elas usam o **envelope**, e a falha delas é
> um objeto, não `null`. A regra do § 8 cobre o caso pelo ramo `live === false`, então o
> desenho não mudou — só o inventário estava impreciso.

### 4.2 `live` é um discriminador confiável — verificado, não presumido

Nas 7 funções com fallback, **sem exceção**, o padrão é:

```ts
if (raw) return { live: true,  … };   // raw = apiFetch != null
return       { live: false, … };      // mock ou vazio
```

Auditei os 15 pontos que atribuem `live` no arquivo: **`live: true` é definido se e
somente se `apiFetch` devolveu não-nulo**. E **nenhuma interface de payload da API
declara um campo `live`** — a única interface com `live` é `TrendResult` (L391), que é o
**tipo de retorno do frontend**, não um contrato do backend. Portanto não existe caso em
que uma resposta bem-sucedida traga `live: false`.

Este fato é o que torna a decisão do § 8 provadamente segura, e é a razão pela qual ela
pode viver numa única função em vez de em 21 call sites.

---

## 5. Inventário completo dos call sites

21 chamadas de `withCache`, em 21 funções distintas. `buildFilterQuery` (L148) monta
`channels`, `brands` (**ordenadas**, portanto determinísticas), `date_from`/`date_to` ou
`ref_month`, e `compare`. **A identidade de filtro está completa em todas as chaves** —
não encontrei nenhum cache compartilhado indevidamente entre identidades distintas.

| L | função | chave do cache | filtros na chave |
|---|---|---|---|
| 291 | `fetchOverview` | `overview:${qs}` | canal, marca, período, comparação |
| 333 | `fetchBrands` | `brands:${qs}` | canal, marca, período, comparação |
| 347 | `fetchMonthly` | `monthly:${marketplace}` | canal (o endpoint é `months_back=6` fixo) |
| 425 | `fetchTrend` | `trend:${granularity}:${qs}` | **granularidade** + canal, marca, período, comparação |
| 608 | `fetchProdutosML` | `produtos-ml:${qs}` | marca, bucket, sinal, status, velocidade, **limit, offset, sort_by, sort_dir** |
| 657 | `fetchProdutosShopee` | `produtos-shopee:${qs}` | idem (fotografia, sem data) |
| 671 | `fetchProdutosTikTok` | `produtos-tk:${qs}` | idem |
| 713 | `fetchProdutosMLSummary` | `produtos-ml-summary:${qs}` | idem, sem paginação |
| 733 | `fetchProdutosTikTokSummary` | `produtos-tk-summary:${qs}` | idem |
| 742 | `fetchProdutosShopeeSummary` | `produtos-sh-summary:${qs}` | idem |
| 922 | `fetchCanais` | `canais:${qs}` | canal, marca, período, comparação |
| 1083 | `fetchFinanceiro` | `financeiro:${qs}` | canal, marca, período, comparação |
| 1183 | `fetchQuality` | `quality:${qs}` | canal, marca, período, comparação |
| 1340 | `fetchBrandDetail` | `brand-detail:${brand}:${month}` | marca + competência mensal |
| 1416 | `fetchPedidos` | `pedidos:${qs}` | canal, marca, período |
| 1489 | `fetchInteligencia` | **`"inteligencia"`** (constante) | nenhum — a tela não tem filtros globais |
| 1555 | `fetchOperacoes` | **`"operacoes"`** (constante) | nenhum — idem |
| 1672 | `fetchRegioesSummary` | `regioes-summary:${qs}` | canal, marca, período + **UF** |
| 1683 | `fetchRegioesByUf` | `regioes-by-uf:${qs}` | canal, marca, período + **UF** |
| 1694 | `fetchRegioesByBrand` | `regioes-by-brand:${qs}` | canal, marca, período (o backend **não** aceita UF aqui) |
| 1705 | `fetchRegioesTrend` | `regioes-trend:${qs}` | canal, marca, período (idem) |

### 5.1 Funções exportadas que **não** usam cache

Continua verdade, e é intencional:

| L | função | por quê | consequência |
|---|---|---|---|
| 518 | `fetchExecutiveSummary` | bloco de insights, sem fallback de mock: se falhar, `data` vem `null` e o componente mostra aviso discreto | **é a única fonte da Gerencial cujo retry funciona hoje** |
| 1243 | `fetchTempoReal` | a tela faz polling com máquina de 5 estados; cachear sabotaria o tempo real | retry manual funciona |

Total: **23 funções `fetchX` exportadas**, 21 com cache, 2 sem.

### 5.2 Achado colateral — `fetchMonthly` é código morto

`fetchMonthly` tem **zero consumidores** em `app/` e `src/`. Não é defeito de cache e
**não deve ser removida no PF1** — fica registrada para limpeza futura, em gate próprio.

---

## 6. Matriz por superfície

Colunas: função pública · chave · sucesso · falha · fallback · campo `live` · botão de
retry · retry muda identidade React · retry muda chave de cache · risco atual · política
recomendada.

| Superfície | função | chave | sucesso | falha | fallback | `live` | retry | muda ident. React | muda chave cache | risco atual | política |
|---|---|---|---|---|---|---|---|---|---|---|---|
| **Gerencial** (KPIs) | `fetchOverview` | `overview:${qs}` | `{live:true,meta,data}` | `{live:false,mock}` | **sim** | sim | sim (hook) | sim | **não** | **mock cacheado 5 min; retry inerte** | não cachear `live:false` |
| **Gerencial** (marcas) | `fetchBrands` | `brands:${qs}` | `{live:true,…}` | `{live:false,mock}` | **sim** | sim | sim (hook) | sim | **não** | idem | idem |
| **Gerencial** (séries) | `fetchTrend` | `trend:${gran}:${qs}` | `{live:true,…}` | `{live:false,data:[]}` | sim (vazio, não mock) | sim | sim (hook) | sim | **não** | **degradado vazio cacheado** | idem |
| **Gerencial** (resumo) | `fetchExecutiveSummary` | — | payload | `null` | não | não | sim (hook) | sim | n/a | **nenhum** | manter sem cache |
| **Canais** | `fetchCanais` | `canais:${qs}` | `{live:true,…}` | `{live:false,mock,channelRows:[]}` | **sim** | sim | sim | sim | **não** | idem | idem |
| **Produtos ML** | `fetchProdutosML` | `produtos-ml:${qs}` | payload | **`null`** | não | não | **não tem** | n/a | n/a | **`null` cacheado; sem retry para sair** | não cachear `null` |
| **Produtos TikTok** | `fetchProdutosTikTok` | `produtos-tk:${qs}` | payload | **`null`** | não | não | **não tem** | n/a | n/a | idem | idem |
| **Produtos Shopee** | `fetchProdutosShopee` | `produtos-shopee:${qs}` | payload | **`null`** | não | não | **não tem** | n/a | n/a | idem | idem |
| **Pareto ML** | `fetchProdutosMLSummary` | `produtos-ml-summary:${qs}` | payload | **`null`** | não | não | **não tem** | n/a | n/a | idem | idem |
| **Pareto TikTok** | `fetchProdutosTikTokSummary` | `produtos-tk-summary:${qs}` | payload | **`null`** | não | não | **não tem** | n/a | n/a | idem | idem |
| **Pareto Shopee** | `fetchProdutosShopeeSummary` | `produtos-sh-summary:${qs}` | payload | **`null`** | não | não | **não tem** | n/a | n/a | idem | idem |
| **Financeiro** | `fetchFinanceiro` | `financeiro:${qs}` | `{live:true,…}` | `{live:false,mock}` | **sim** | sim | sim | sim | **não** | mock cacheado | não cachear `live:false` |
| **Qualidade** | `fetchQuality` | `quality:${qs}` | `{live:true,…}` | `{live:false,mock}` | **sim** | sim | sim | sim | **não** | idem | idem |
| **Marca** | `fetchBrandDetail` | `brand-detail:${brand}:${month}` | payload | **`null`** | não | não | **não tem** | n/a | n/a | **`null` cacheado; sem retry** | não cachear `null` |
| **Pedidos** | `fetchPedidos` | `pedidos:${qs}` | payload | **`null`** | não | não | sim | sim | **não** | **`null` cacheado; retry inerte** | idem |
| **Inteligência** | `fetchInteligencia` | `"inteligencia"` | payload | **`null`** | não | não | sim | sim | **não** | idem | idem |
| **Operações** | `fetchOperacoes` | `"operacoes"` | payload | **`null`** | não | não | sim | sim | **não** | idem | idem |
| **Regiões** summary | `fetchRegioesSummary` | `regioes-summary:${qs}` | payload | **`null`** | não | não | sim | sim | **não** | idem | idem |
| **Regiões** by-UF | `fetchRegioesByUf` | `regioes-by-uf:${qs}` | payload | **`null`** | não | não | sim | sim | **não** | idem | idem |
| **Regiões** by-brand | `fetchRegioesByBrand` | `regioes-by-brand:${qs}` | payload | **`null`** | não | não | sim | sim | **não** | idem | idem |
| **Regiões** trend | `fetchRegioesTrend` | `regioes-trend:${qs}` | payload | **`null`** | não | não | sim | sim | **não** | idem | idem |
| **monthly** | `fetchMonthly` | `monthly:${marketplace}` | `{live:true,data}` | `{live:false,mock}` | **sim** | sim | **sem consumidor** | n/a | n/a | latente (código morto) | não cachear `live:false` |
| **Tempo Real** | `fetchTempoReal` | — | `{data,live:true}` | `null` | não | sim | sim | n/a | n/a | **nenhum** | manter sem cache |

**Resumo do risco:** **21 de 21 funções cacheadas** armazenam a falha. Em **12** a falha
é `null`; em **9** é um objeto com `live: false` (7 fallback de demonstração + 2 envelope). **Nenhuma** tela muda a
chave do cache no retry. **Três superfícies não têm retry nenhum** — Produtos (3 tabelas
+ 3 resumos Pareto) e Marca — e por isso, nelas, a única saída da falha cacheada é
esperar o TTL ou recarregar a página.

---

## 7. Alternativas avaliadas

### Alternativa A — predicado `shouldCache` por call site

`withCache(key, fn, shouldCache?)`, com cada call site declarando o próprio critério.

- **Clareza:** boa localmente; o critério fica ao lado do dado.
- **Risco de esquecer um call site:** **alto e silencioso.** São 21 sites, e um site
  esquecido mantém exatamente o bug de hoje, sem nenhum sinal. Se o parâmetro for
  opcional, o *default* continua sendo "cacheia tudo" — a política insegura permanece
  como padrão.
- **Reconhecer live/mock:** cada site repetiria `res.live === true` ou `res != null`,
  duplicando 21 vezes uma regra que é a mesma.
- **Veredito:** rejeitada como forma principal. É a forma certa apenas para a
  *exceção* (§ 8.1).

### Alternativa B — resultado discriminado (`success` / `degraded` / `failure`)

- **Segurança de tipos:** a melhor de todas.
- **Tamanho da alteração:** muda o **formato público de todas as 23 `fetchX`** e,
  em cascata, os 11 consumidores em `app/` e `src/`, incluindo `useGerencialSources` e
  os 5 componentes da Gerencial.
- **Impacto nos contratos públicos:** aciona **diretamente um gatilho de stop-loss**
  (§ 15).
- **Veredito:** rejeitada. É a solução mais correta em abstrato e a mais desproporcional
  para este defeito.

### Alternativa C — cachear a resposta bruta de `apiFetch`

Mover o cache para dentro de `apiFetch`, indexado pelo path, cacheando só o payload
antes da transformação.

- **Duplicação:** eliminaria a chave duplicada (path × `qs`).
- **Compatibilidade com normalização:** boa — as transformações são puras e baratas de
  repetir.
- **Abrangência:** **é aqui que falha.** `apiFetch` é chamada também por
  `fetchExecutiveSummary` e `fetchTempoReal`, que **deliberadamente não têm cache**.
  Cachear em `apiFetch` **introduziria cache no polling do Tempo Real** — uma regressão
  funcional grave, silenciosa, numa tela que existe justamente para mostrar o agora.
- **Veredito:** rejeitada.

### Alternativa D — invalidar no retry (`invalidate(prefix)` ou `forceRefresh`)

- **Resolve `null` e fallback?** Só no caminho do retry. Uma navegação ou uma primeira
  visita dentro dos 5 min de um `null` cacheado continuaria servindo a falha. E as três
  superfícies **sem** retry (Produtos, Marca) não teriam correção alguma.
- **Risco de wiring incompleto:** **alto.** Exigiria tocar 8 páginas mais o hook da
  Gerencial e acertar 13 pontos de botão. É precisamente a classe de defeito que o
  patch final do U5 teve de corrigir — três pontos de falha que atualizavam
  `error`/`loading` mas nunca concluíam `resolvedKey`.
- **Necessidade de tocar todas as páginas:** sim.
- **Veredito:** rejeitada como solução principal. Pode ser considerada *depois*, se
  aparecer necessidade de refresh explícito com cache válido — que não é o problema de
  hoje.

---

## 8. Decisão recomendada

**Inverter o default dentro de `withCache`: armazenar somente resultados comprovadamente
bem-sucedidos.** Uma única função muda; **zero call sites** são tocados.

A regra, em uma frase: **não armazene se o resultado for `null`/`undefined`, ou se for um
objeto que declara `live: false`.**

Em pseudocódigo, para fixar a intenção (a implementação é da Task 2/2):

```
withCache(key, fn):
    hit = cacheGet(key)
    if hit !== undefined: return hit          // inalterado — preserva 0/false/""/[]
    result = await fn()
    if isCacheable(result): cacheSet(key, result)
    return result                             // devolvido SEMPRE, cacheado ou não

isCacheable(v):
    if v === null or v === undefined: return false        // cobre as 12 funções "cru"
    if v is object and v.live === false:  return false    // cobre as 7 com fallback e as 2 com envelope
    return true                                           // 0, false, "", [], payloads
```

Duas propriedades importam mais que o código:

1. **O valor continua sendo devolvido ao chamador.** A correção muda *o que é
   memoizado*, nunca *o que a tela recebe*. Nenhum fallback comercial deixa de aparecer,
   nenhum estado visual muda, nenhuma ausência vira zero.
2. **A sentinela de miss não é tocada.** `hit !== undefined` fica como está, e por isso
   o Caso D continua verde por construção.

### Por que esta é a menor solução completa

- **Menor:** um arquivo, uma função, ~4 linhas. Nenhuma assinatura pública muda; nenhuma
  página muda; nenhum contrato de dados muda; nenhuma dependência entra.
- **Completa:** cobre **as 21 funções cacheadas de uma vez**, incluindo as **três
  superfícies que não têm botão de retry** — que as Alternativas A e D não alcançam. E
  cobre as duas formas de falha, porque `null` e `live:false` são, comprovadamente
  (§ 4.2), as **únicas** duas formas que uma falha assume neste arquivo.
- **Segura por default:** um call site novo, escrito no futuro por quem não leu este
  documento, **já nasce com a política correta**. Alternativa A tem a propriedade
  oposta.
- **Precisa, não heurística:** `live` não é adivinhado. Auditei os 15 pontos de
  atribuição e nenhuma interface de payload da API declara `live` — a única é
  `TrendResult`, tipo de retorno do frontend. `live: true` ⟺ `apiFetch` devolveu
  não-nulo.

### 8.1 Onde a Alternativa A ainda cabe — como exceção, não como regra

O item 5 do contrato pede que uma resposta degradada só seja cacheada se o contrato
**declarar conscientemente** que ela é cacheável. Duas leituras precisam ser separadas:

- **HTTP 200 com seções parciais** (por exemplo Regiões, em que um dos quatro endpoints
  falha e os outros respondem) é **resposta bem-sucedida da API** e **permanece
  cacheável**. A parcialidade já é tratada na camada de tela — o Gate U4 separou
  explicitamente "seção indisponível (`null`)" de "seção vazia com sucesso (`[]`)".
  Cada endpoint tem chave própria, então o que falhou não é cacheado e o que respondeu é.
- **`fetchTrend` na falha** devolve `{ live: false, data: [] }` — vazio, não mock. Sob a
  regra, **não é cacheado**, o que é o correto: é indisponibilidade, não uma série vazia
  de verdade.

Se algum dia aparecer um caso legítimo de "degradado que **deve** ser cacheado", o
parâmetro opcional da Alternativa A entra **naquele único site**, como exceção explícita
e revisável — nunca como default. **Não há nenhum caso desses hoje**, e a Task 2/2 **não
deve** introduzir o parâmetro preventivamente (§ 15, "arquitetura genérica excessiva").

---

## 9. Contrato de cache

| # | regra | como a decisão do § 8 cumpre |
|---|---|---|
| 1 | resposta live bem-sucedida pode ficar 5 min em cache | `CACHE_TTL` e `cacheGet` intocados |
| 2 | `null` nunca armazenado | `isCacheable` rejeita `null` |
| 3 | erro de HTTP, rede, timeout ou JSON inválido nunca armazenado | os quatro colapsam em `null` em `apiFetch`; regra 2 os cobre |
| 4 | fallback mock nunca armazenado como resposta válida | `isCacheable` rejeita `live === false` |
| 5 | degradado/parcial só se o contrato declarar | declarado no § 8.1: 200 parcial **é** cacheável; `live:false` **não é** |
| 6 | `0`, `false` e `[]` de sucesso continuam válidos e cacheáveis | sentinela `undefined` preservada; Caso D |
| 7 | retry após falha realmente consulta a API | nada foi cacheado, então o próximo `withCache` é MISS |
| 8 | sucesso das outras fontes continua em cache no retry composto | as chaves são por fonte; só a que falhou é MISS |
| 9 | não limpar o cache globalmente | nenhuma invalidação é introduzida |
| 10 | sem retry automático, backoff, polling ou dependência | nenhum dos quatro entra |
| 11 | não alterar contratos, métricas, fallbacks ou estados visuais | o valor devolvido ao chamador é idêntico |
| 12 | não transformar ausência em zero | `null` continua `null` para a tela |
| 13 | sem arquitetura genérica excessiva | uma função privada, sem parâmetro novo |

---

## 10. Contrato de retry

O retry **não muda**. Nenhuma página, nenhum botão, nenhum `retryKey`, nenhum
`requestKey`. O que muda é que ele passa a funcionar:

- `retryKey` continua sendo **apenas** identidade React, como os Gates U4/U5
  desenharam;
- a chave do cache continua **sem** `retryKey` — de propósito: é o que permite que as
  fontes bem-sucedidas continuem servidas do cache num retry composto (Caso F);
- **é a ausência da falha no cache** que faz o retry consultar a rede, não uma
  invalidação;
- as três superfícies sem botão (Produtos, Marca) passam a se recuperar sozinhas na
  próxima montagem ou troca de filtro, sem esperar o TTL. **Adicionar botão de retry a
  elas não é escopo do PF1** — fica registrado como melhoria de UX separada.

---

## 11. Testes planejados para a Task 2/2

`withCache`/`cacheGet`/`cacheSet` são **privados do módulo** e `api-client.ts` não
exporta nada com "cache" no nome. Para haver teste puro, a Task 2/2 precisa de **um
único export aditivo**: o predicado `isCacheable` (ou nome equivalente). É a alteração
mínima que torna a regra testável sem stub de `globalThis.fetch` e sem mexer em
assinatura pública de `fetchX`.

### 11.1 Testes puros — cobertos pelo predicado e por um relógio injetável

| # | teste | classificação |
|---|---|---|
| 1 | sucesso (payload) é cacheável | puro |
| 2 | `null` não é cacheável | puro |
| 3 | `undefined` não é cacheável | puro |
| 4 | `{live:false, data:mock}` não é cacheável | puro |
| 5 | `{live:true, …}` é cacheável | puro |
| 6 | `0` é cacheável | puro |
| 7 | `false` é cacheável | puro |
| 8 | `[]` de sucesso é cacheável | puro |
| 9 | `""` é cacheável | puro |
| 10 | objeto **sem** campo `live` é cacheável (os 14 call sites "cru") | puro |
| 11 | TTL expira e a fonte é reconsultada | puro (relógio injetável) |
| 12 | chaves distintas não colidem | puro |
| 13 | granularidade `day`/`week`/`month` não colide (`trend:${gran}:${qs}`) | puro |
| 14 | filtros distintos não colidem (canal, marca, período, comparação, UF, paginação, ordenação) | puro |
| 15 | **primeira falha seguida de sucesso imediato**: a 2ª chamada bate na rede e devolve o dado real | puro (contador de chamadas) |
| 16 | duas chamadas concorrentes com a mesma chave: **classificar** que não há dedup in-flight e que o comportamento **não regrediu** | puro |
| 17 | `null` × zero segue distinguível (regra 12 do contrato) | puro |
| 18 | nenhum `setTimeout`/`setInterval`/backoff introduzido em `api-client.ts` | puro (asserção estática) |
| 19 | as 21 chaves seguem estáveis e determinísticas (`brands` ordenadas) | puro |

### 11.2 Exigem wiring/componente/navegador

| # | teste | por quê |
|---|---|---|
| 20 | **retry da Gerencial após falha**: a fonte que falhou volta à rede, as 4 bem-sucedidas vêm do cache | precisa do hook e dos 6 efeitos; wiring |
| 21 | **retry de Inteligência/Operações após falha** consulta a rede | precisa do efeito + botão; wiring ou navegador |
| 22 | fallback de demonstração continua **aparecendo** na tela (regra 11) | componente/navegador |
| 23 | "modo demonstração" da Gerencial continua sinalizado (`substitutedByMock`) | componente |
| 24 | Tempo Real continua **sem** cache e com polling intacto | navegador |
| 25 | Produtos/Marca se recuperam na remontagem sem esperar o TTL | navegador |

**Recomendação de arquivo:** colocar os testes puros em
**`apps/web/tests/request-freshness.test.ts`**, que **já está registrado** no script
`test` e cujo tema — frescor de requisição — é exatamente adjacente. Isso evita tocar
`package.json`, que está bloqueado até o OM1 entrar (§ 16). Se, ao implementar, ficar
claro que um arquivo novo é indispensável, ele **só pode ser registrado depois do OM1**.

---

## 12. Arquivos previstos para a Task 2/2

**Mínimo (recomendado):**

1. `apps/web/src/lib/api-client.ts` — o predicado novo, a guarda em `withCache` e um
   export aditivo para teste. **Nenhuma assinatura de `fetchX` muda.**
2. `apps/web/tests/request-freshness.test.ts` — testes puros, em arquivo já registrado.

**Se e somente se a validação exigir, com justificativa registrada:**

3. `apps/web/tests/<novo>.test.ts` + `apps/web/package.json` — **bloqueado até o OM1
   entrar em `origin/main`**.
4. `docs/FRONTEND_CACHE_RETRY_PLAN.md` — registro do resultado.
5. `docs/PROJECT_STATUS.md` — **fora do escopo da Task 1/2**; na Task 2/2, somente
   depois de coordenar com o OM1.

**Nenhum arquivo** em `apps/api`, `pipelines`, `db`, `migrations`, `.sql`, e **nenhuma**
página de `app/` precisa mudar.

---

## 13. Critérios de aceite da Task 2/2

1. Casos A e B da prova **invertem**: a 2ª chamada consulta a rede.
2. Casos C, D, E permanecem idênticos.
3. Caso F: a fonte que falhou consulta a rede; as 4 bem-sucedidas continuam vindo do
   cache.
4. Nenhuma assinatura pública de `fetchX` alterada.
5. Nenhuma página de `app/` alterada.
6. `null` continua distinguível de zero em todas as telas.
7. Fallbacks de demonstração continuam aparecendo e continuam sinalizados como
   demonstração.
8. Tempo Real e resumo executivo continuam **sem** cache.
9. Zero dependência nova; `package-lock.json` intocado.
10. Nenhum retry automático, backoff ou polling introduzido.
11. `npm test`, `npm run typecheck` e `npm run build` verdes.
12. QA em navegador confirmando o retry funcionando em pelo menos Inteligência (padrão
    `null`) e Gerencial (padrão fallback).

---

## 14. Riscos

| # | risco | severidade | mitigação |
|---|---|---|---|
| R1 | `live` deixar de ser discriminador confiável se alguém adicionar um payload com `live` | média | teste 10 fixa "objeto sem `live` é cacheável"; § 4.2 registra a auditoria dos 15 pontos |
| R2 | mais chamadas de rede em cenário de API instável, porque a falha não é mais memoizada | **baixa e intencional** | é o objetivo do gate; sem retry automático, o volume é o de cliques do usuário |
| R3 | `fetchTrend` na falha devolve `data: []`, que poderia ser lido como série vazia legítima | baixa | ele carrega `live:false`; a regra o exclui e o § 8.1 registra a decisão |
| R4 | corrigir a sentinela de miss por engano (`if (hit)`) e quebrar zero/`[]` | **alta se ocorrer** | Caso D + testes 6-9 falham alto; § 8 proíbe explicitamente tocar a sentinela |
| R5 | wiring incompleto | **eliminado por desenho** | zero call sites tocados |
| R6 | ausência de dedup in-flight virar visível com mais MISS | baixa | teste 16 classifica sem corrigir; fora do escopo |
| R7 | conflito com o OM1 em `package.json` | média | § 11 evita `package.json`; § 16 coordena |
| R8 | Produtos e Marca seguem sem botão de retry | baixa | melhoria de UX registrada, fora do PF1 |

---

## 15. Stop-loss

Parar e replanejar, **sem ampliar silenciosamente**, se aparecer qualquer um destes:

1. a correção exigir mudar o formato público de todas as funções `fetchX`
   (Alternativa B);
2. exigir alterar fallbacks comerciais ou o que a tela exibe;
3. revelar que dado live e mock **não** são distinguíveis — **hoje são**, via `live`
   (§ 4.2); se isso mudar, a premissa central cai;
4. exigir editar mais do que **duas** páginas — o desenho prevê **zero**;
5. surgir cache server-side/SSR não mapeado — **hoje não existe** (`unstable_cache`,
   `revalidate`, `next/cache`, `force-cache`: zero ocorrências);
6. a correção exigir tocar `package.json` antes do OM1 entrar.

---

## 16. Coordenação com OM1 e V3

- A instância do **Oráculo** está integrando o **OM1** e pode avançar `origin/main`,
  **inclusive em `apps/web/package.json`**. Por isso o § 11 recomenda testes em arquivo
  **já registrado**, e o § 12 marca `package.json` como **bloqueado até o OM1 entrar**.
- Nesta Task 1/2 **não houve** `merge`, `rebase`, `cherry-pick` nem tentativa de
  integrar a branch do Oráculo. A base auditada é `e675948`.
- O **V3-1A** está versionado em `e675948`. O PF1 é a **dívida de plataforma registrada
  no §20.10 do plano do V3**, anterior ao V3-1A e **não causada por ele**.
- **PF1 vem antes do V3-1B.** O V3-1B segue bloqueado pelo contrato **BE6** e o V3-2
  pelo **BE5**. Nenhum deles foi iniciado.
- `docs/PROJECT_STATUS.md` **não foi tocado** nesta task, conforme a restrição de
  concorrência.

---

## 17. Estado

**PF1 Task 1/2 — CONCLUÍDA.** Auditoria completa das 23 funções `fetchX`, dos 21 call
sites de `withCache`, das 13 telas/componentes com "Tentar novamente" e da relação entre
`retryKey` e chave de cache. Defeito provado em 7 casos executáveis, fora do
repositório. Quatro alternativas comparadas e uma recomendada com justificativa.

**PF1 Task 2/2 — CONCLUÍDA** em 19/08/2026: implementada em três arquivos, 38 testes
novos, suíte em 745/745, e QA em navegador provando o retry em Gerencial, Inteligência,
Operações, Produtos e Marca. Detalhes no § 18. **Nenhum commit, push ou deploy.**

---

## 18. Task 2/2 — implementação e validação local (19/08/2026)

**Estado: `IMPLEMENTADA E VALIDADA LOCALMENTE — NÃO VERSIONADA`.**

Base de implementação: `e675948`. Durante a rodada, `origin/main` avançou para
`2821d61` (§ 22) e **não foi integrado**, conforme a restrição de concorrência.

### 18.1 Diff funcional

Uma linha removida, nove linhas de código adicionadas, em
`apps/web/src/lib/api-client.ts`. Nada mais.

```diff
-  return cacheSet(key, result);
+  if (isCacheableApiResult(result)) cacheSet(key, result);
+  return result;
```

mais o predicado novo:

```ts
export function isCacheableApiResult(value: unknown): boolean {
  if (value === null || value === undefined) return false;
  if (typeof value === "object" && "live" in value && (value as { live?: unknown }).live === false) {
    return false;
  }
  return true;
}
```

`withCache` passou a devolver `result` sempre, cacheado ou não. Antes ele devolvia
`cacheSet(...)`, que era o mesmo valor — a diferença é que agora o armazenamento é
condicional e a devolução não.

**Não mudou:** `CACHE_TTL`, `cacheGet`, a sentinela `hit !== undefined`, `apiFetch`,
as 21 chaves, as 23 assinaturas públicas, nenhuma página, nenhum fallback, nenhum
contrato de dados. `withCache`, `_cache`, `cacheGet` e `cacheSet` **continuam
privados** — a única exposição nova é o predicado, com nome de contrato.

### 18.2 Correção de um erro da Task 1/2: existe um TERCEIRO padrão

A Task 1/2 classificou `fetchInteligencia` e `fetchOperacoes` como padrão "cru", com
falha igual a `null`. **Estava errado.** As duas devolvem um envelope:

```ts
return { data: raw, live: raw != null };
```

Na falha isso é `{ data: null, live: false }` — um objeto, não `null`. A correção do
§ 8 **cobre esse caso pelo ramo `live === false`**, então o desenho não mudou; mas o
inventário estava impreciso e a reauditoria pegou isso antes da implementação. Os três
padrões reais, agora verificados:

| padrão | nº | retorno em falha | regra que o cobre |
|---|---|---|---|
| cru | **12** | `null` | `value === null` |
| fallback | **7** | `{ live: false, mock/vazio }` | `live === false` |
| **envelope** | **2** | `{ data: null, live: false }` | `live === false` |

12 + 7 + 2 = 21. A tabela do § 6 permanece válida em tudo, menos na coluna "retorno em
falha" de Inteligência e Operações, corrigida aqui.

### 18.3 Reauditoria dos 21 call sites, pós-implementação

- **12 retornos crus `null`** deixaram de ser cacheados.
- **9 objetos com `live === false`** (7 fallback + 2 envelope) deixaram de ser cacheados.
- **Respostas live continuam cacheadas** nos três padrões.
- `fetchExecutiveSummary` **permanece sem cache** — verificado no fonte e provado no QA
  (ela rebateu na rede no retry da Gerencial, `1 → 2`, enquanto as fontes cacheadas
  ficaram paradas).
- `fetchTempoReal` **permanece sem cache** — verificado no fonte.
- `fetchMonthly` **permanece intocada**, mesmo sem consumidor.
- **Nenhuma assinatura pública mudou** (23 funções, nenhuma ganhou `forceRefresh`,
  `skipCache`, `noCache`, `shouldCache` ou `invalidate`).
- **Nenhuma chave perdeu filtro, paginação ou granularidade** — as 21 chaves foram
  comparadas literalmente com a lista esperada, em teste.
- **Nenhum fallback sem `live: false`** foi encontrado. A premissa do desenho está
  confirmada: dos 8 pontos que atribuem `live: true`, **todos** estão guardados por
  `if (raw)`, e a única interface com campo `live` é `TrendResult`, tipo de retorno do
  frontend.

### 18.4 Testes — 38 novos, em arquivo já registrado

Todos em `apps/web/tests/request-freshness.test.ts`, seção **"PF1 — cache de respostas
e retry"**. `package.json` **não foi tocado** — decisão da Task 1/2 que se provou
acertada, porque o commit `2821d61` do OM1 mexeu justamente em `package.json` e
`package-lock.json` (§ 22).

**Limitação estrutural encontrada, contornada e declarada.** `api-client.ts` **não pode
ser importado em runtime** por `node --test`: ele importa `./mock-data` e outros três
módulos **sem extensão**, e o resolver ESM do Node não completa extensão. Verifiquei
empiricamente, inclusive que `--experimental-specifier-resolution` não existe mais no
Node 24. Não é novidade do PF1: `tests/regioes.test.ts` L3-L5 **já documenta exatamente
essa restrição**, e é a razão pela qual **todos** os testes deste projeto importam
`api-client.ts` apenas como `import type`. Consequência: chamar `fetchProdutosML` com
`global.fetch` controlado, como a Task 2/2 previa, é **impossível nesta base** sem (a)
pôr extensão `.ts` nos imports de um módulo que todas as páginas usam — o que exigiria
mexer em `tsconfig.json`, fora da autorização — ou (b) mover o predicado para um módulo
próprio, o que seria um quarto arquivo.

O contorno tem duas metades, e **nenhuma é "presença textual da guarda"**:

1. **Guarda de deriva.** O teste lê o código-fonte real de `isCacheableApiResult` e de
   `withCache` e exige que ele seja **exatamente** o esperado, caractere por caractere.
   Provei que a guarda funciona: sabotei o predicado no fonte trocando a comparação
   explícita por `if (!value)` — o truthiness genérico que o contrato proíbe — e o teste
   **reprovou**; restaurei e voltou a passar.
2. **Harness de transcrição.** As duas funções são transcritas no arquivo de teste e os
   casos comportamentais rodam contra elas, com relógio e contador de chamadas. A guarda
   1 é o que torna a transcrição equivalente à produção.

A prova ponta a ponta **pelas funções públicas reais** roda no QA em navegador (§ 18.5),
contra o bundle de produção — que é, aliás, uma prova mais forte que um stub de `fetch`.

Cobertura: 13 casos do predicado (`null`, `undefined`, `{live:false}`,
`{live:false,data:[]}`, envelope, `{live:true}`, `0`, `false`, `""`, `[]`, `[0]`, objeto
sem `live`, e `live` valendo `undefined`/`0`/`"false"` — só o literal `false` reprova);
os três padrões de call site com falha→sucesso; erro de rede e JSON inválido; sucesso
cacheado nos três padrões; `0`/`false`/`""`/`[]` sobrevivendo como valores cacheados;
`null` × zero; TTL nos dois sentidos; 12 chaves distintas sem colisão; as 21 chaves
reais; determinismo da ordenação de marcas; concorrência registrada; e 6 guardas de
escopo (sem temporizador, sem backoff, sem invalidação global, `apiFetch` intacto,
maquinaria privada, `cacheSet` chamada em um único lugar e sob o predicado, o
`return cacheSet(...)` incondicional não pode voltar, as duas funções sem cache seguem
sem cache, 23 assinaturas intactas).

**Suíte completa: 745/745** (era 707).

### 18.5 QA local em navegador — comportamento provado contra o bundle de produção

Chromium 149 dirigido por Playwright 1.62.1, ambos do cache local; **nenhuma dependência
instalada**; build de produção servida localmente. Técnica: a **primeira** requisição de
cada endpoint falha com HTTP 500 e as seguintes respondem 200. A asserção central é o
**contador de requisições** — antes do PF1 ele ficava em 1 para sempre.

| Superfície | prova | resultado |
|---|---|---|
| **Gerencial** | 1ª de `overview` falha; clicar "Tentar novamente" | **`overview` 1 → 2 requisições**; o valor live **R$ 987.654** aparece; a tela sai do estado degradado |
| **Gerencial** (retry composto) | as outras 4 fontes cacheadas respondem 200 | `brands` 1→1, `trend` 3→3, `canais` 1→1, `quality` 1→1 — **o sucesso continuou vindo do cache**, exatamente como o contrato pede |
| **Gerencial** (sem cache) | `executive-summary` | **1 → 2** — segue sem cache, como antes |
| **Inteligência** | 1ª falha; "Tentar novamente" | **1 → 2 requisições**; o payload real substituiu o erro |
| **Operações** | 1ª falha; "Tentar novamente" | **1 → 2 requisições** |
| **Produtos** | 1ª montagem falha; navegar para outra rota e voltar | **1 → 2 requisições**; o produto real aparece **sem esperar cinco minutos**; confirmado que a rota **não tem** botão de retry |
| **Marca** | mesma prova de remontagem | **1 → 2 requisições** |
| **Tempo Real** | `fetchTempoReal` no fonte | **sem `withCache`** — segue fora do cache, polling inalterado |

Validações transversais: **zero erro de aplicação** (as únicas mensagens de console são
os HTTP 500 que o próprio QA injeta), **zero erro de hidratação**, **zero request de
escrita**, e **zero escape de rede** — os 8 requests de API foram todos cumpridos pelo
interceptor, com `0` em `requestfailed`.

**Fallback e estado visual inalterados.** A banda de KPIs da Gerencial continua
declarando indisponibilidade com a redação própria dela — *"O agregado do período não
respondeu nesta carga — GMV, Pedidos, Ticket, Investimento e ROAS ficam indisponíveis.
Os demais blocos abaixo seguem com as próprias fontes."* — e **não** apresenta mock como
dado real. Nenhuma interface foi redesenhada.

Artefatos (scripts, screenshots, JSON de resultados, log do servidor) ficaram somente em
`%TEMP%`. Servidor e navegador encerrados; nenhum Chromium remanescente; o Chrome do
sistema não foi tocado.

### 18.6 Chamadas de rede adicionais — intencionais, e onde ocorrem

A correção **aumenta** o número de chamadas em cenário de API instável. Isso é o
objetivo do gate, não um efeito colateral: **falha e fallback não devem ser cacheados**.
As chamadas adicionais ocorrem em três situações, todas dirigidas pelo usuário:

1. **clique explícito em "Tentar novamente"** — nas 8 telas que têm o botão;
2. **remontagem ou navegação** em **Produtos** e **Marca**, que não têm botão — é
   justamente por isso que elas passam a se recuperar sozinhas;
3. **mudança real de filtro**, que já criava identidade nova antes.

**Não há retry automático, backoff nem polling**, então o volume é limitado a ações do
usuário. E **o sucesso continua protegido pelo TTL de cinco minutos**: duas chamadas
idênticas bem-sucedidas seguem fazendo uma única requisição.

### 18.7 Concorrência in-flight — dívida separada, não criada aqui

`withCache` memoiza o **valor** resolvido, não a promise, então duas chamadas
simultâneas com a mesma chave consultam a fonte duas vezes. É **anterior ao PF1** e
**não foi criado por ele**; corrigir exigiria armazenar promises, fora do escopo deste
gate. Há teste registrando o comportamento — que classifica, não corrige.

### 18.8 Validações

`npm test` **745/745** (38 novos), `npm run typecheck` limpo, `npm run build`
compilando, `git diff --check` limpo, `package.json` e `package-lock.json` **sem diff**,
**zero dependência nova**, **zero arquivo fora dos três autorizados**, scan de
secrets/DSN/token/IP privado/PII/caminhos pessoais nas 570 linhas novas e no documento
**sem ocorrência**. Detector visual não se aplica: **nenhuma alteração visual**.

### 18.9 Riscos remanescentes

| # | risco | severidade | situação |
|---|---|---|---|
| R1 | alguém adicionar um payload de API com campo `live` | média | teste fixa "objeto sem `live` é cacheável"; § 18.3 registra a auditoria |
| R2 | mais chamadas de rede sob API instável | **baixa, intencional** | § 18.6; limitada a cliques e remontagem |
| R4 | trocar a sentinela de miss por truthiness | **alta se ocorrer** | guarda de deriva reprova, e isso foi **provado** sabotando o fonte |
| R6 | ausência de dedup in-flight | baixa | § 18.7, classificada |
| R8 | Produtos e Marca seguem sem botão de retry | baixa | melhoria de UX fora do PF1; a recuperação por remontagem já está provada |
| **R9** | `api-client.ts` não é importável em runtime por `node --test` | **média** | § 18.4. Contornado por guarda de deriva + transcrição + QA em navegador. **Correção estrutural sugerida, não autorizada nesta rodada:** mover o predicado para um módulo próprio sem a cadeia de `mock-data` — é o precedente que o próprio repo documenta em `regioes-query.ts`. Isso exigiria um quarto arquivo |

### 18.10 Estado final

**PF1 está implementado e validado localmente. Ainda NÃO está versionado.** Nenhum
commit, push, merge, rebase ou deploy foi feito. O working tree tem exatamente três
arquivos: `api-client.ts`, `request-freshness.test.ts` e este documento.

---

## 19. Contrato final de cache

Consolidando o § 9 com o que a implementação e o QA provaram:

| # | regra | provado por |
|---|---|---|
| 1 | sucesso live fica 5 min em cache | teste de TTL + QA (`brands`/`trend`/`canais`/`quality` paradas no retry) |
| 2 | `null` nunca armazenado | 12 funções; teste de padrão cru |
| 3 | HTTP, rede, timeout e JSON inválido nunca armazenados | teste dos três modos de falha |
| 4 | fallback mock nunca armazenado | 7 funções; teste de fallback + QA da Gerencial |
| 4b | envelope `{data:null, live:false}` nunca armazenado | 2 funções; teste de envelope + QA de Inteligência e Operações |
| 5 | degradado/parcial só se declarado | § 8.1: 200 parcial **é** cacheável; `live:false` **não é** |
| 6 | `0`, `false`, `""`, `[]` de sucesso continuam cacheáveis | 5 valores × teste dedicado |
| 7 | retry após falha consulta a API | QA: 1 → 2 em Gerencial, Inteligência, Operações, Produtos e Marca |
| 8 | sucesso das outras fontes fica em cache no retry composto | QA da Gerencial: 4 fontes paradas |
| 9 | sem invalidação global | guarda de escopo: `_cache.clear()` ausente |
| 10 | sem retry automático, backoff, polling ou dependência | guarda de escopo: sem temporizador |
| 11 | contratos, métricas, fallbacks e estados visuais intactos | 23 assinaturas + QA da redação da Gerencial |
| 12 | ausência não vira zero | teste `null` × zero |
| 13 | sem arquitetura genérica excessiva | uma função privada guardada, um export |

## 20. Contrato final de retry

Inalterado, e é isso que se queria: nenhuma página, nenhum botão, nenhum `retryKey`,
nenhum `requestKey` foi tocado. `retryKey` segue sendo **apenas** identidade React, e a
chave do cache segue **sem** ele — de propósito, porque é o que permite às fontes
bem-sucedidas continuarem no cache num retry composto. **É a ausência da falha no cache
que faz o retry consultar a rede**, não uma invalidação.

## 21. Critérios de aceite — situação

Os 12 critérios do § 13 estão atendidos: casos A e B invertidos (QA); C, D e E
idênticos; caso F com a fonte que falhou rebatendo e as 4 bem-sucedidas em cache;
nenhuma assinatura pública alterada; nenhuma página alterada; `null` distinguível de
zero; fallbacks aparecendo e sinalizados; Tempo Real e resumo executivo sem cache; zero
dependência nova e lockfile intocado; sem retry automático; `npm test`, `typecheck` e
`build` verdes; QA em navegador confirmando Inteligência (padrão cru) e Gerencial
(padrão fallback).

## 22. Coordenação com OM1 — `origin/main` avançou

Durante esta rodada `origin/main` passou de `e675948` para
**`2821d618c2ee4368d8ee999c03b2c537782ba39c`** — *feat(web): adiciona servidor mcp
read-only do oraculo*, 25 arquivos, incluindo **`apps/web/package.json`,
`apps/web/package-lock.json`**, `docs/PROJECT_STATUS.md` e
`apps/web/tests/inteligencia-v3-wiring.test.ts`.

**Não houve integração:** nenhum `merge`, `rebase`, `cherry-pick` ou restauração. O PF1
segue baseado em `e675948`, e o HEAD local está **1 commit atrás** de `origin/main`.

**Zero colisão** entre o commit do OM1 e os três arquivos do PF1. E a decisão da Task
1/2 de pôr os testes em arquivo **já registrado** evitou exatamente o conflito que
existiria agora, porque o OM1 alterou `package.json`.

A integração e o versionamento do PF1 ficam para uma rodada própria, depois de coordenar
com o OM1.
