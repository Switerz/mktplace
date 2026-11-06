"use client";

/**
 * Cabecalho da Gerencial — UMA linha (UI-DENSIDADE-1, escolha do proprietario).
 *
 * Titulo, filtros e frescor numa unica barra sticky, como a Torre do Atacado.
 * Sairam o subtitulo ("Como estamos, onde investigar…"), que nao informava
 * nada, e o selo de periodo, que repetia o filtro de periodo logo ao lado. O
 * periodo continua dito ao leitor de tela junto do titulo.
 *
 * Os controles de filtro chegam como `children`: a fonte de verdade continua
 * sendo a URL via `useGlobalFilters`, na pagina — este componente nao guarda
 * nenhum estado paralelo de filtro.
 *
 * Fragment de proposito (Gate V2-4): a barra sobe como filha DIRETA do
 * container da pagina, cuja caixa abrange todo o conteudo; com um wrapper
 * proprio em volta, o `sticky` so' colaria dentro da altura do cabecalho.
 */
import { LayoutDashboard } from "lucide-react";
import Freshness from "@/components/ui/Freshness";

interface Props {
  periodLabel: string;
  refreshedAt: string | null;
  /** `null` enquanto nenhuma fonte principal esta fresca — nunca afirma live. */
  live: boolean | null;
  loading: boolean;
  children: React.ReactNode;
}

export default function GerencialHeader({ periodLabel, refreshedAt, live, loading, children }: Props) {
  return (
    <>
      {/* `-mx-4 -mt-4` + `px-4`: a barra sangra ate a borda do container (que
          tem 16px de respiro) e cola no topo sem faixa acima. Fundo em token,
          quase opaco, para o conteudo passar por baixo sem vazar leitura. */}
      <div className="sticky top-0 z-30 -mx-4 -mt-4 px-4 py-1.5 tc-barra-vidro border-b border-line">
        <div className="flex items-center justify-between gap-x-3 gap-y-1.5 flex-wrap">
          <h1 className="flex shrink-0 items-center gap-2 text-lg font-bold text-ink">
            <span
              aria-hidden="true"
              className="flex h-8 w-8 items-center justify-center rounded-lg bg-accent-soft text-accent ring-1 ring-inset ring-accent/15"
            >
              <LayoutDashboard className="h-4 w-4" strokeWidth={1.75} />
            </span>
            {/* UX-VIDRO-2: o degrade fica no texto, nao no <h1> inteiro (o icone tem fundo proprio). */}
            <span className="tc-titulo-texto">Visão Gerencial</span>
            <span className="sr-only"> · {periodLabel}</span>
          </h1>
          {/* Titulo + filtros (~1.200px) + selo so' cabem numa linha a partir de
              ~1.800px. Abaixo disso os filtros descem INTEIROS para uma linha
              propria (order-last + basis-full), em vez de espremer o canal com
              rolagem e empilhar marca e periodo um em cada linha. */}
          <div className="order-last flex min-w-0 basis-full items-start gap-x-3 gap-y-1.5 flex-wrap min-[1800px]:order-none min-[1800px]:basis-0 min-[1800px]:flex-1">
            {children}
          </div>
          {/* refreshed_at e selo saem SO' da resposta compativel com o filtro
              atual — nunca da requisicao anterior (`live` e' null ate la'). */}
          <Freshness live={live} refreshedAt={live != null ? refreshedAt : null} loading={loading} />
        </div>
      </div>
    </>
  );
}
