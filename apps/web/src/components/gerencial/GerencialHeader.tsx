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
import { LayoutDashboard } from "lucide-react";
import Freshness, { PeriodChip } from "@/components/ui/Freshness";

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
      {/* UX-TORRE-B2B-1: icone + titulo a esquerda, escopo em selos a direita.
          O periodo e o frescor continuam na primeira linha da tela — so'
          deixaram de ser uma frase entre o titulo e os filtros. */}
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <div className="flex min-w-0 items-center gap-3">
          <span
            aria-hidden="true"
            className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-accent-soft text-accent ring-1 ring-inset ring-accent/15"
          >
            <LayoutDashboard className="h-5 w-5" strokeWidth={1.75} />
          </span>
          <div className="min-w-0">
            <h1 className="text-xl font-bold text-ink">Visão Gerencial</h1>
            <p className="text-sm text-ink-muted">Como estamos, onde investigar e para onde ir a seguir.</p>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-1.5">
          <PeriodChip label={periodLabel} />
          {/* refreshed_at e selo saem SO' da resposta compativel com o filtro
              atual — nunca da requisicao anterior (`live` e' null ate la'). */}
          <Freshness live={live} refreshedAt={live != null ? refreshedAt : null} loading={loading} />
        </div>
      </div>

      {/* Com os controles compartilhados a 44x44px, esta faixa passou de ~29%
          para 32% do viewport no tablet. O ajuste e de ESPACAMENTO — padding
          vertical e gap entre linhas quando os filtros embrulham —, nunca do
          alvo: reduzir o botao para preservar a altura da faixa desfaria
          exatamente o que este patch corrige. `top-0` e o comportamento do
          V2-4 seguem intactos.
          UX-TORRE-2: fundo em token (`bg-canvas`), que acompanha o tema; no
          mobile os controles recolhem dentro de `GlobalFilterControls`. */}
      <div className="sticky top-0 z-30 -mx-6 px-6 py-1.5 bg-canvas/95 backdrop-blur supports-[backdrop-filter]:bg-canvas/80 border-b border-line">
        <div className="flex items-start justify-between gap-x-3 gap-y-1.5 flex-wrap">{children}</div>
      </div>
    </>
  );
}
