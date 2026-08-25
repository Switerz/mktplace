"use client";

import type { BrandArrivalContext } from "@/lib/brand-arrival-context";
import {
  buildReturnHref, originLabel, returnCtaLabel, returnPreservesGlobalFilters,
} from "@/lib/brand-arrival-context";
import DrilldownContextLine from "@/components/drilldown/DrilldownContextLine";
import DataQualityNote from "@/components/drilldown/DataQualityNote";
import DrilldownCta from "@/components/drilldown/DrilldownCta";

interface Props {
  /** Contexto já validado. `null` ⇒ nada é renderizado (acesso direto). */
  ctx: BrandArrivalContext | null;
  periodLabel: string;
  /** Combina o destino com os filtros globais atuais (`mergeFilteredHref`). */
  buildHref: (href: string) => string;
}

/**
 * "Chegando quente" na página de Marca (Gate G3, Task 2 — desenho em
 * docs/DRILLDOWN_ARCHITECTURE.md §8.4). Bloco compacto que explica QUAL sinal
 * motivou a navegação a partir do detalhe marca × canal de Canais.
 *
 * Invariantes:
 * - **zero número**: toda a descrição vem de texto allowlisted do código; a
 *   querystring transporta só identificadores e nunca é fonte de métrica;
 * - não é modal e não abre diálogo algum; não esconde nem substitui os KPIs;
 * - não afirma que os dados foram carregados (não exibe `refreshed_at` — o
 *   frescor continua sendo comunicado pelo cabeçalho da própria página);
 * - "Ir para a seção" aparece **somente** quando a página realmente evidencia
 *   o sinal; caso contrário a limitação é declarada explicitamente;
 * - sem contexto válido ⇒ `null` (nenhum placeholder, nenhum espaço vazio).
 */
export default function BrandArrivalBanner({ ctx, periodLabel, buildHref }: Props) {
  if (!ctx) return null;

  return (
    <section
      aria-label="Contexto da navegação"
      className="bg-violet-50/60 border border-violet-100 rounded-2xl p-4 flex flex-col gap-2"
    >
      <div>
        {/* A origem é nomeada: "chegou por X" sem dizer DE ONDE deixa o
            analista sem saber para onde voltar nem que evidência esperar. */}
        <p className="text-xs font-semibold text-violet-700 uppercase tracking-wider">
          Você chegou de {originLabel(ctx)} por
        </p>
        <p className="text-sm text-slate-700">{ctx.description}</p>
      </div>

      {/* Canal + período pela mesma linha de contexto dos drill-downs (G2).
          `refreshedAt` fica null de propósito: este bloco não declara frescor. */}
      <DrilldownContextLine leading={ctx.channelLabel} periodLabel={periodLabel} refreshedAt={null} />

      {/* Limitação quando esta página não evidencia o sinal — nunca prometer
          custo, frete, cancelamento ou referência de ROAS que ela não tem. */}
      {!ctx.hasEvidence && <DataQualityNote note={ctx.unavailableNote} />}

      <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
        {ctx.hasEvidence && ctx.section && ctx.sectionLabel && (
          <DrilldownCta href={`#${ctx.section}`} ariaLabel={`${ctx.sectionLabel} nesta página`}>
            {ctx.sectionLabel} ↓
          </DrilldownCta>
        )}
        {/* Retorno à evidência. Canais é filter-aware, então os filtros globais
            são preservados por buildHref e marca/canal do contexto vencem. O
            retorno à Inteligência vai DIRETO: aquela rota não herda filtro
            global, e o href tem âncora, que mergeFilteredHref destruiria (ver
            `returnPreservesGlobalFilters`). Nenhum `ctx_*` é repropagado nos
            dois casos. */}
        <DrilldownCta
          href={returnPreservesGlobalFilters(ctx) ? buildHref(buildReturnHref(ctx)) : buildReturnHref(ctx)}
        >
          {returnCtaLabel(ctx)} →
        </DrilldownCta>
      </div>

      {/* Quando HÁ evidência, a ressalva de escopo continua visível, mas em tom
          discreto — o número exibido na seção é da marca, não a comparação. */}
      {ctx.hasEvidence && ctx.unavailableNote && (
        <p className="text-xs text-slate-500">{ctx.unavailableNote}</p>
      )}
    </section>
  );
}
