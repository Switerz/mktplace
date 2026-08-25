"use client";

/**
 * Cabecalho compacto + barra de filtros sticky da Gerencial V2 (Gate V2-1,
 * Task C).
 *
 * Os controles de filtro chegam como `children`: a fonte de verdade continua
 * sendo a URL via `useGlobalFilters`, na pagina — este componente nao guarda
 * nenhum estado paralelo de filtro.
 *
 * `scroll-margin-top` nas ancoras da pagina resolve o unico risco real da barra
 * sticky: navegar por ancora e o titulo ficar escondido atras dela.
 */
import LiveStatusBadge from "@/components/LiveStatusBadge";
import { fmtRefreshedAt } from "@/lib/filters/format";

interface Props {
  periodLabel: string;
  refreshedAt: string | null;
  /** `null` enquanto nenhuma fonte principal esta fresca — nunca afirma live. */
  live: boolean | null;
  loading: boolean;
  children: React.ReactNode;
}

export default function GerencialHeader({ periodLabel, refreshedAt, live, loading, children }: Props) {
  // Fragment de proposito (Gate V2-4, correcao terminal): cabecalho e barra saem
  // como IRMAOS no fluxo do container da Gerencial.
  //
  // Antes, os dois viviam dentro de um `<div className="flex flex-col gap-3">`
  // deste componente. `position: sticky` e' limitado pela caixa do elemento PAI,
  // e aquele pai terminava imediatamente apos a barra: ela tinha curso praticamente
  // zero e rolava para fora da tela junto com o cabecalho. Medido no QA do V2-3:
  // topo da barra em -729px (desktop), -597px (tablet) e -631px (mobile).
  //
  // Com o Fragment, o pai passa a ser o container da pagina (`max-w-[1440px] …
  // flex flex-col gap-4` em `app/page.tsx`), cuja caixa abrange todo o conteudo —
  // e a barra permanece em `top: 0` durante todo o scroll. `app/page.tsx` NAO
  // precisou mudar: o `GerencialHeader` ja era filho direto desse container.
  //
  // Mesma solucao estrutural validada no `PageHeader` das outras dez rotas, sem
  // fundir os dois componentes: os headings e a composicao diferem (aqui `<h1>`
  // e um subtitulo proprio) e um contrato generico esconderia essa diferenca.
  return (
    <>
      <div className="flex items-start justify-between gap-3 flex-wrap">
        <div className="min-w-0">
          <h1 className="text-xl font-bold text-gray-900">Visão Gerencial</h1>
          <p className="text-sm text-slate-500">Como estamos, onde investigar e para onde ir a seguir.</p>
          <p className="text-xs text-slate-500 mt-0.5">
            Período: {periodLabel}
            {/* refreshed_at e badge saem SO' da resposta compativel com o filtro
                atual — nunca da requisicao anterior. */}
            {live != null && refreshedAt && <> · Atualizado em {fmtRefreshedAt(refreshedAt)}</>}
          </p>
        </div>
        {live != null ? (
          <LiveStatusBadge live={live} />
        ) : loading ? (
          <span className="text-xs text-slate-500 bg-slate-100 border border-slate-200 rounded-lg px-3 py-1.5 font-medium">
            Atualizando dados…
          </span>
        ) : null}
      </div>

      {/* Com os controles compartilhados a 44x44px, esta faixa passou de ~29%
          para 32% do viewport no tablet. O ajuste e de ESPACAMENTO — padding
          vertical e gap entre linhas quando os filtros embrulham —, nunca do
          alvo: reduzir o botao para preservar a altura da faixa desfaria
          exatamente o que este patch corrige. `top-0` e o comportamento do
          V2-4 seguem intactos. */}
      <div className="sticky top-0 z-30 -mx-6 px-6 py-1.5 bg-[#f8f7ff]/95 backdrop-blur supports-[backdrop-filter]:bg-[#f8f7ff]/80 border-b border-violet-100/70">
        <div className="flex items-start justify-between gap-x-3 gap-y-1.5 flex-wrap">{children}</div>
      </div>
    </>
  );
}
