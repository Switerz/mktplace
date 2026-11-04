/**
 * Aba CX (menu principal, 04/11/2026) — atendimento ao cliente nos
 * marketplaces. Primeira versao: so' Shopee.
 *
 * Ordem de leitura: chat (Saude da Conta, oficial e com a meta da Shopee) →
 * devolucoes (coleta propria) → cancelamentos → o que ainda falta, dito na
 * tela para a ausencia nunca parecer zero.
 *
 * O servidor so' le os filtros da URL e desenha cabecalho e formulario; os
 * dados sao buscados pelo navegador em `CxConteudo` (a API vive atras do
 * login, via `/torre-api`). Nenhum dado demonstrativo.
 */
import PageContainer from "@/components/layout/PageContainer";
import PageHeader from "@/components/layout/PageHeader";
import { JANELAS_DIAS, lerFiltrosCx, type FiltrosCx } from "@/lib/cx";
import { ALL_BRAND_KEYS, ALL_BRAND_OPTIONS } from "@/lib/filters/brands";

import CxConteudo from "./CxConteudo";

export const dynamic = "force-dynamic";

type SearchParams = Promise<Record<string, string | string[] | undefined>>;

export default async function CxPage({ searchParams }: { searchParams: SearchParams }) {
  const filtros = lerFiltrosCx(await searchParams, ALL_BRAND_KEYS);
  return (
    <PageContainer>
      <PageHeader title="CX" subtitle="Atendimento ao cliente · Shopee" filters={<Filtros f={filtros} />} />
      <CxConteudo filtros={filtros} />
    </PageContainer>
  );
}

/** Filtro por formulario GET: funciona sem JS e mantem a URL como fonte de verdade. */
function Filtros({ f }: { f: FiltrosCx }) {
  const campo =
    "min-h-11 rounded-md border border-line bg-surface px-2 text-sm text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent";
  return (
    <form method="get" action="/cx" className="flex flex-wrap items-end gap-3" data-testid="cx-filtros">
      <label className="flex flex-col gap-1 text-xs font-medium text-ink-muted">
        Marca
        <select name="brands" defaultValue={f.marca ?? ""} className={campo}>
          <option value="">Todas as marcas</option>
          {ALL_BRAND_OPTIONS.map((b) => (
            <option key={b.value} value={b.value}>
              {b.label}
            </option>
          ))}
        </select>
      </label>
      <label className="flex flex-col gap-1 text-xs font-medium text-ink-muted">
        Período
        <select name="dias" defaultValue={String(f.dias)} className={campo}>
          {JANELAS_DIAS.map((d) => (
            <option key={d} value={d}>
              Últimos {d} dias
            </option>
          ))}
        </select>
      </label>
      <button
        type="submit"
        className="min-h-11 rounded-md border border-line-strong px-3 text-sm font-semibold text-ink hover:bg-raised focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
      >
        Aplicar
      </button>
    </form>
  );
}

