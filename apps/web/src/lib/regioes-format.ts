import type { CoverageLevel } from "./api-client";

/** Denominador 0/ausente vira "N/A" — nunca "0%" (mesma regra do backend,
 * ver app/services/regioes_service.py::_pct). */
export function fmtPctOrNA(pct: number | null): string {
  if (pct == null) return "N/A";
  return `${pct.toFixed(1)}%`;
}

export function coverageLabel(level: CoverageLevel): string {
  switch (level) {
    case "ok": return "Cobertura OK";
    case "partial": return "Cobertura parcial";
    case "low": return "Cobertura baixa";
    case "not_applicable": return "N/A";
  }
}

export function coverageBadgeClass(level: CoverageLevel): string {
  switch (level) {
    case "ok": return "bg-emerald-50 text-emerald-700 border-emerald-200";
    case "partial": return "bg-amber-50 text-amber-700 border-amber-200";
    case "low": return "bg-rose-50 text-rose-700 border-rose-200";
    case "not_applicable": return "bg-slate-50 text-slate-400 border-slate-200";
  }
}

/**
 * TikTok nunca tem linha em marts.fact_marketplace_region_daily (nenhuma
 * fonte mapeada expoe UF do pedido) — se o canal foi pedido mas nao tem
 * cobertura regional, o aviso precisa deixar claro que os numeros exibidos
 * NAO incluem TikTok, para nunca ser lido como "TikTok vendeu R$0" (ver
 * docs/regional_design_draft.md).
 */
export function semCoberturaAviso(channelsSemCobertura: string[]): string | null {
  if (channelsSemCobertura.length === 0) return null;
  const nomes: Record<string, string> = { tiktok: "TikTok Shop", ml: "Mercado Livre", shopee: "Shopee" };
  const labels = channelsSemCobertura.map((c) => nomes[c] ?? c);
  return (
    `${labels.join(", ")} não tem cobertura regional (nenhuma fonte mapeada expõe UF do pedido) — ` +
    `não incluído nos números abaixo. Isso NÃO significa venda zero, apenas ausência de dado regional.`
  );
}

/** Participação (%) de um valor sobre o total do escopo filtrado — null
 * quando o total e' 0 (nunca divide por zero, nunca mostra 0% enganoso). */
export function fmtShareOfTotalPct(value: number, total: number): number | null {
  if (total <= 0) return null;
  return Math.round((value / total) * 1000) / 10;
}
