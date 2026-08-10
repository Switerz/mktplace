"use client";

/**
 * Cabecalho de pagina do V2 (Gate V2-2, Task 2/2) para as dez superficies fora
 * da Gerencial.
 *
 * Resolve tres inconsistencias que as dez rotas repetiam:
 *
 * 1. **Ordem da narrativa.** O periodo/frescor aparecia DEPOIS da barra de
 *    filtros, puxado para cima com `-mt-3` — um ajuste de pixel que quebrava em
 *    quebra de linha. Aqui a linha de escopo pertence ao cabecalho, acima dos
 *    filtros, e o `-mt-3` desaparece.
 * 2. **Filtros nao fixos.** Nas paginas que herdam filtros globais, rolar uma
 *    tabela longa escondia o proprio escopo do que estava sendo lido. A barra
 *    agora e' sticky — e SO' quando a pagina realmente tem filtros (Tempo Real,
 *    Inteligencia e Operacoes nao tem, e nao ganham barra vazia).
 * 3. **Nivel de titulo.** Mantem `<h2>`: o `<h1>` da pagina e' o do shell
 *    (`Torre de Controle`). Nao promovemos para `<h1>` de proposito — isso
 *    agravaria a divida U6-04 (dois `<h1>`), que e' do shell e esta fora deste
 *    gate.
 *
 * Nao guarda estado: filtros, badge e linha de escopo chegam prontos da pagina,
 * cuja fonte de verdade continua sendo a URL via `useGlobalFilters`.
 */
interface Props {
  title: string;
  subtitle: string;
  /** Escopo do que esta na tela: periodo, fonte, frescor. */
  scopeLine?: React.ReactNode;
  /** Badge de frescor/live. A pagina decide se existe — nunca afirmamos live aqui. */
  status?: React.ReactNode;
  /** Controles de filtro. Presentes => barra sticky. Ausentes => sem barra. */
  filters?: React.ReactNode;
}

export default function PageHeader({ title, subtitle, scopeLine, status, filters }: Props) {
  // Fragment de proposito (Gate V2-3, correcao consolidada): cabecalho e barra
  // saem como IRMAOS no fluxo do `PageContainer`.
  //
  // Antes, os dois viviam dentro de um `<div className="flex flex-col gap-3">`
  // proprio deste componente. `position: sticky` e' limitado pela caixa do
  // ELEMENTO PAI, e aquele pai terminava imediatamente apos a barra: ela
  // "colava" apenas dentro da altura do cabecalho e rolava para fora da tela
  // junto com ele. Medido em /canais antes da correcao: com `scrollY=507` no
  // desktop, o topo da barra estava em -336px; no mobile, -667px com
  // `scrollY=900`. Ou seja, o sticky existia no CSS e nao existia na pratica.
  //
  // Com o Fragment, o pai passa a ser o container da pagina inteira, que tem a
  // altura de todo o conteudo — e a barra permanece em `top: 0` durante todo o
  // scroll. Nao ha wrapper novo, nenhuma rota replica filtros, e o `gap` entre
  // cabecalho e barra passa a ser o do proprio `PageContainer`.
  return (
    <>
      <div className="flex flex-col gap-1">
        <div className="flex items-start justify-between gap-3 flex-wrap">
          <div className="min-w-0">
            <h2 className="text-xl font-bold text-gray-900">{title}</h2>
            <p className="text-sm text-slate-500">{subtitle}</p>
            {scopeLine && <p className="text-xs text-slate-500 mt-0.5">{scopeLine}</p>}
          </div>
          {status}
        </div>
      </div>

      {filters && (
        // `-mx-4 sm:-mx-6` + `px-4 sm:px-6` fazem a faixa sangrar ate a borda do
        // container, para a barra fixa nao parecer um card solto. O fundo e'
        // quase opaco com blur de apoio: o conteudo passa por baixo sem vazar
        // leitura. `z-30` fica abaixo do dialogo (portalizado no body) e acima
        // de todo o conteudo da pagina.
        <div className="sticky top-0 z-30 -mx-4 sm:-mx-6 px-4 sm:px-6 py-2 bg-[#f8f7ff]/95 backdrop-blur supports-[backdrop-filter]:bg-[#f8f7ff]/80 border-b border-violet-100/70">
          <div className="flex items-start justify-between gap-3 flex-wrap">{filters}</div>
        </div>
      )}
    </>
  );
}
