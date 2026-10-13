# UX-TORRE-B2B-1 — benchmark visual da Torre B2B

> **Audiência:** quem mantém a Torre Marketplace (engenharia e design).
>
> **Referência:** a Torre B2B do grupo (repositório privado). Ela foi lida em
> 13/10/2026 só como referência de sistema visual. Nenhum código, regra de
> negócio, query, credencial ou dado foi copiado.
>
> Este repositório é **público**. Por isso o documento registra só padrões
> visuais genéricos: nada de caminho de arquivo, trecho de código ou detalhe de
> domínio do B2B.
>
> A decisão de sistema que resultou daqui está em `DESIGN.md`.

## 1. O que o benchmark faz

| Eixo | Padrão observado |
|---|---|
| Stack | Next/React/Tailwind com Recharts e `lucide-react` |
| Topologia | Sidebar fixa à esquerda (expandida ~240px, recolhida ~56px) e área de conteúdo rolável; cabeçalho de página sticky por rota |
| Sidebar | Navy escuro, grupos com rótulo pequeno em caixa-alta, um ícone por item; o item ativo combina fundo translúcido, barra fina à esquerda e ícone mais grosso; recolhe sozinha em tela média e vira gaveta no mobile |
| Tokens | Variáveis CSS em hex com escala de marca índigo; tema escuro preparado mas não ativado |
| Tipografia | Sans geométrica via Google Fonts; rótulos de 10–11px predominam; KPI em 20–24px bold, sempre com algarismos tabulares |
| Cabeçalho | Tile de ícone com gradiente, título de 16px, subtítulo de 12px e ações à direita |
| Filtros | Barra sticky de uma linha; cada filtro é um gatilho de ~30px que mostra a contagem ("3 marcas") e abre um popover com checkbox e "Limpar" |
| KPIs | Grade de 2 colunas que sobe para 5–6; rótulo pequeno em caixa-alta, valor bold, delta com seta verde/vermelha e ícone discreto |
| Painéis | Raio de ~12px, borda de 1px, cabeçalho com título, subtítulo e controles, sem sombra |
| Tabelas | Cabeçalho pequeno em caixa-alta sobre fundo neutro, linhas sem zebra, números à direita, total destacado com borda superior dupla, coluna e cabeçalho fixos quando necessário |
| Gráficos | Recharts carregado sob demanda; grade tracejada só horizontal; eixo sem linha; tooltip escuro; legenda em círculos |
| Estados | Vazio com ícone apagado e frase curta; carregando com skeleton; erro em faixa tingida com "Tentar novamente" |
| Responsivo | Colunas de tabela somem por breakpoint; tabelas rolam na horizontal |

## 2. O que foi adaptado

| Padrão do benchmark | Como entrou na Torre | Onde |
|---|---|---|
| Sidebar navy agrupada com ícones | Navy com fundo violeta (`--tc-shell`), escura nos **dois** temas, com o mesmo marcador triplo do item ativo e trilho de 64px | `shell/Sidebar.tsx`, `shell/NavList.tsx` |
| Recolhimento automático em tela média | Trilho abaixo de 1280px; a escolha só persiste quando feita em tela larga | `Sidebar.tsx` |
| Cabeçalho com ícone | Tile de 40px **sólido** (`accent-soft`), sem gradiente; é o mesmo ícone da sidebar | `layout/PageHeader.tsx`, `gerencial/GerencialHeader.tsx` |
| Filtro com gatilho que mostra o valor | Marca e Período viram gatilho + popover, com os MESMOS botões de antes (`aria-pressed`, 44px) | `ui/Popover.tsx`, `BrandFilter.tsx`, `DateRangeFilter.tsx` |
| KPI denso com ícone | Rótulo, ícone tingido pela série, número de 24px e pílula de delta | `gerencial/KpiBand.tsx`, `ui/StatCard.tsx` |
| Painel com borda e sem sombra | Adotado no sistema todo; sombra só em hover e em camadas flutuantes | `ui/Panel.tsx` e componentes da Gerencial |
| Tabela densa sem zebra | Linha `py-2.5`, degrau `text-data` (13px), cabeçalho sobre `raised` | Financeiro, Canais, Gerencial |
| Gráfico carregado sob demanda | Gráfico do Financeiro via `next/dynamic` | `app/financeiro/page.tsx` |
| Lucide | `lucide-react@1.53.0`, versão exata | `package.json` |

## 3. O que NÃO foi copiado, e por quê

| Do benchmark | Decisão | Motivo |
|---|---|---|
| Rótulos de 10–11px | **Não.** Piso de 12px mantido | Decisão de acessibilidade do UX-TORRE-1; a densidade vem do espaçamento |
| Gatilhos de ~30px | **Não.** Alvo de 44px mantido | Contrato V3 travado em teste; a barra ficou densa por caber em **uma** linha |
| Fonte do benchmark via `@import` | **Não.** Inter via `next/font`, autohospedada | Algarismos tabulares reais, nenhuma requisição externa em runtime e identidade própria |
| Paleta índigo/slate | **Não.** Violeta da Torre + navy-violeta | Identidade da Torre Marketplace |
| Tokens em hex, tema escuro inativo | **Não.** Tokens em canais RGB e dois temas completos | Contrato de tema do UX-TORRE-1 (`tema.test.ts`) |
| Tile de ícone com gradiente | **Não.** Tile sólido | Gradiente decorativo é anti-padrão no `DESIGN.md` |
| Formatação monetária compacta | **Não alterada** | Os formatadores da Torre foram preservados; a regra deste gate é dado idêntico |
| Cores literais nos componentes | **Não.** Só tokens | `tema.test.ts` recusa literal de paleta nas rotas tematizadas |
| Item de menu piscando | **Não** | Movimento decorativo, sem estado |
| Regras e telas do domínio B2B | **Não** | Não se aplicam a marketplace |

## 4. Medidas antes × depois

Os valores finais, as capturas pareadas e a comparação célula a célula ficam no
relatório do gate, fora do repositório, e na descrição do PR.
