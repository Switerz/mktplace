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

## O que este sistema não faz

- Não inverte cores por CSS. Cada papel tem valor próprio por tema.
- Não usa media query para o tema (`darkMode: "class"`): preferência manual
  precisa poder sobrepor o sistema operacional.
- Não toca em métrica, fórmula, filtro ou contrato de API. O redesenho é da
  camada visual; os números vêm do mesmo payload.
