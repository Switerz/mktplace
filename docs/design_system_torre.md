# Sistema visual da Torre — guia de migração

Escrito para quem vai migrar as próximas telas (`/`, `/canais`, `/financeiro`,
depois o resto). A definição do sistema está em [`DESIGN.md`](../DESIGN.md) §7;
aqui está o como fazer.

## O que existe

**Tokens** (`apps/web/app/globals.css`) — um token é um **papel**, não uma cor.
O valor muda entre claro e escuro; nenhuma tela escreve hex.

| Papel | Classe Tailwind |
|---|---|
| fundo da página | `bg-canvas` |
| cartão | `bg-surface` |
| superfície elevada / faixa interna | `bg-raised` |
| borda / borda de ênfase | `border-line` · `border-line-strong` |
| texto principal / secundário / terciário | `text-ink` · `text-ink-muted` · `text-ink-faint` |
| destaque (violeta da marca) | `bg-accent` · `text-accent` · `bg-accent-soft` · `text-accent-ink` |
| tinta sobre o destaque cheio | `text-accent-on` |
| sucesso / atenção / crítico | `ok` · `warn` · `crit` (+ `-soft` para fundo, `-ink` para texto) |
| série de gráfico 1–6 | `chart-1` … `chart-6`, ou `temaDoGrafico()` para SVG |

**Componentes** (`apps/web/src/components/ui/`):

| Componente | Para quê |
|---|---|
| `PageBar` | a faixa **única** de cabeçalho da tela: título, controles, metadados |
| `Panel` | a unidade de superfície; tem `titulo`, `descricao`, `acao`, `rodape`, `semPadding` |
| `KpiTile` | célula da faixa de KPIs: rótulo, valor, comparação, tom, `destaque` |
| `Chip` | selo compacto de metadado (frescor, modo de carga, estado) |
| `Note` | ressalva de uma linha, com fundo tingido e moldura inteira |
| `Disclosure` | "Como ler esta medição" — o destino da metodologia longa |
| `Segmented` | radiogroup acessível (setas, roving tabindex, alvo de 44px) |
| `SortHeader` | cabeçalho de coluna ordenável, tematizado |
| `tone.ts` | o vocabulário de severidade: `neutro`, `bom`, `atencao`, `critico`, `ausente` |

## Como migrar uma tela

1. Troque literais por tokens. O que **não** pode sobrar: `bg-white`,
   `text-slate-*`, `text-gray-*`, `border-violet-*`, `bg-*-50`, e qualquer
   variante `dark:` — o token já muda sozinho.
2. Colapse o cabeçalho em **um** `PageBar`. Título, seletores e frescor na
   mesma faixa; nada de três blocos introdutórios empilhados.
3. Troque cartões soltos por `Panel`. Cartão não contém cartão: aviso interno
   usa `Note`, não uma segunda moldura.
4. Mande metodologia, limitação e explicação longa para `Disclosure`. **Mover,
   nunca apagar**: a ressalva continua existindo, com a mesma palavra.
   Ressalva que qualifica um número (a LDR reconstruída, por exemplo) fica
   colada ao número, em `Note` compacto — essa não vai para o recolhível.
5. Gráfico: use `temaDoGrafico(resolvido)` de `@/lib/chart-theme`. O recharts
   pinta SVG e precisa de cor resolvida; `var(--...)` num atributo SVG não é
   confiável.
6. Remova a rota de `ROTAS_TEMATIZADAS` — quer dizer, **acrescente** a rota à
   lista em `src/lib/theme.ts`. Enquanto ela não estiver lá, a tela renderiza
   no tema claro mesmo com o escuro ligado.

## Regras que o gate deixou explícitas

- **Cor de severidade só com regra de negócio por trás.** `neutro` não é
  ausência de estilo: é a afirmação de que nada diz se aquele número é bom ou
  ruim. `ausente` é o quarto tom e existe porque "não medido" ≠ "zero".
- **`ok` / `warn` / `crit` cheios são preenchimento** (barra, ponto, borda).
  Texto usa a variante `-ink`.
- **Piso tipográfico de 12px.** Tabela densa usa o passo `data` (13px); prosa
  usa 14px.
- **Alvo de 44px explícito** (`min-h-11` ou `min-h-[44px]`), nunca por acidente
  de `py-*` + `line-height`. Exceção medida e documentada: os links da sidebar
  do desktop usam 36px (`min-h-9`); o mesmo `NavList` no drawer mobile recebe
  `alvoAmplo` e volta para 44px.
- **`aria-expanded` sempre com `aria-controls`**, e o alvo precisa existir de
  verdade quando o controle aparece.
- **Números tabulares** em toda coluna de valor (`tabular-nums`, já global em
  `table`).

## UX-TORRE-2 — o que a segunda leva acrescentou

Rotas migradas: `/`, `/canais`, `/financeiro` (além de `/expedicao`).

- **Identidade de canal é um papel próprio** (`canal-tiktok`, `canal-ml`,
  `canal-shopee`; `bg-canal-*` ou `temaDoGrafico(...).canais` no SVG). Não é
  série por posição: TikTok é violeta em toda a Torre. No tema claro os valores
  são os mesmos hex de antes.
- **Três vocabulários de cor, sem mistura:** canal (quem é), série (composição,
  ex.: vídeo/live/card, Ads/Frete) e severidade (`ok`/`warn`/`crit`, só com
  limiar de negócio: ROAS, ACOS, taxas %, recompra %). Ponto de KPI é canal ou
  série — nunca âmbar/verde decorativo.
- **Filtros globais** se montam com `GlobalFilterControls`
  (`src/components/layout/`): canal, marca e período no estilo do `Segmented`
  (trilho neutro, ativo em relevo, 44px). No mobile recolhem atrás de um botão
  com o resumo do escopo (`resumoDosFiltros`). `hideCompare` onde a fonte não
  calcula delta (Canais, Financeiro).
- **Diálogo portalizado herda a barreira:** `KpiDrilldownDialog` aplica
  `.tc-tema-claro` quando a rota ainda não migrou; o véu é preto translúcido
  nos dois temas.
- **`ui/SortHeader`** encosta o rótulo das colunas numéricas à direita
  (`flex-row-reverse justify-start`). O `SortableHeader` legado continua literal
  e serve as rotas não migradas.
- **Entrar em `ROTAS_TEMATIZADAS` exige a árvore inteira em tokens:**
  `tests/tema.test.ts` percorre o grafo de imports a partir da página da rota
  e recusa qualquer cor literal (a lista manual da Expedição não escalava).
  Para migrar a próxima rota: acrescente-a à lista e ao mapa `PAGINA_DA_ROTA`
  do teste — ele mostra cada arquivo que ainda falta.

## O que este sistema não faz

- Não inverte cores por CSS. Cada papel tem valor próprio por tema.
- Não usa media query para o tema (`darkMode: "class"`): preferência manual
  precisa poder sobrepor o sistema operacional.
- Não toca em métrica, fórmula, filtro ou contrato de API. O redesenho é da
  camada visual; os números vêm do mesmo payload.

## UX-TORRE-B2B-1 — shell navy e primitives (13/10/2026)

Revisão aprovada pelo Mário com `b2b-gogroup/torre_b2b` como referência de
acabamento. Auditoria em `docs/ux_torre_b2b_benchmark.md`; regras em
`DESIGN.md`. O que mudou para quem desenvolve:

- **Shell navy nos dois temas** (`--tc-shell-*`, `bg-shell`, `text-shell-*`).
  Sidebar com ícone por rota (`ICONE_DA_ROTA` em `shell/NavList.tsx`), trilho
  de 64px abaixo de 1280px, tema no rodapé. A topbar só existe no mobile.
- **Fonte Inter** via `next/font` (variável `--font-sans`), autohospedada.
- **Novo degrau tipográfico nomeado:** `text-data` (13px / 1.35).
- **Primitives novos:** `ui/Popover` (gatilho + painel de filtro),
  `ui/Freshness` + `PeriodChip` (escopo e frescor em selos), `ui/StatCard`
  (KPI não interativo), `ui/StateBlock` + `RetryButton` (vazio / carregando /
  erro / indisponível). `Panel` ganhou `icone` e `id`; `PageHeader` ganhou
  `icon` e `meta`; `Segmented` ganhou `tom="shell"` e `soIcone`.
- **Filtros `variante="menu"`:** `BrandFilter` e `DateRangeFilter` aceitam o
  modo popover usado pela barra comum; as outras rotas seguem no modo `faixa`.
- **`lucide-react@1.53.0`** é o único vocabulário de ícones (versão exata).
