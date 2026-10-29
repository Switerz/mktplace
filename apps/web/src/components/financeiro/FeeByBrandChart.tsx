"use client";

import { Bar, BarChart, CartesianGrid, LabelList, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { useTema } from "@/components/theme/ThemeProvider";
import { temaDoGrafico, type CanalDoGrafico } from "@/lib/chart-theme";

/**
 * UX-TORRE-B2B-1 — taxa do marketplace sobre o GMV, marca x canal.
 *
 * So' le percentuais que a API JA' entrega por linha (`*_avg_fee_pct`): nenhum
 * calculo novo, nenhuma soma entre canais. Cada canal mede uma coisa
 * ligeiramente diferente — o rotulo da legenda diz qual — e por isso as barras
 * ficam LADO A LADO, nunca empilhadas.
 *
 * Cor = identidade do canal. Valor impresso na ponta da barra: o grafico pode
 * ser lido sem tooltip e sem cor.
 */
export interface LinhaTaxa {
  marca: string;
  tiktok: number | null;
  ml: number | null;
  shopee: number | null;
}

export const ROTULO_TAXA: Record<CanalDoGrafico, string> = {
  tiktok: "TikTok · taxas e encargos",
  ml: "ML · tarifa",
  shopee: "Shopee · taxas e encargos",
};

const fmt = (v: unknown) => (typeof v === "number" ? `${v.toFixed(1)}%` : "");

export default function FeeByBrandChart({
  linhas,
  canais,
}: {
  linhas: LinhaTaxa[];
  canais: CanalDoGrafico[];
}) {
  const tema = temaDoGrafico(useTema().resolvido);
  // 12px por barra + respiro por marca: cinco marcas x tres canais ~300px.
  const altura = Math.max(180, linhas.length * (canais.length * 13 + 22) + 36);

  const resumo = linhas
    .map(
      (l) =>
        `${l.marca}: ` +
        canais.map((c) => `${ROTULO_TAXA[c]} ${l[c] == null ? "sem dado" : fmt(l[c])}`).join(", "),
    )
    .join("; ");

  return (
    <div className="flex flex-col gap-3">
      <ul className="flex flex-wrap gap-x-4 gap-y-1" aria-hidden="true">
        {canais.map((c) => (
          <li key={c} className="flex items-center gap-1.5 text-xs text-ink-muted">
            <span className="h-2.5 w-2.5 rounded-sm" style={{ background: tema.canais[c] }} />
            {ROTULO_TAXA[c]}
          </li>
        ))}
      </ul>
      <div role="img" aria-label={`Taxa do marketplace sobre o GMV por marca. ${resumo}`} style={{ height: altura }}>
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={linhas} layout="vertical" margin={{ top: 0, right: 44, bottom: 0, left: 0 }} barGap={2} barCategoryGap="22%">
            <CartesianGrid horizontal={false} stroke={tema.grade} strokeDasharray="3 3" />
            <XAxis
              type="number"
              tickFormatter={(v) => `${v}%`}
              tick={{ fontSize: 12, fill: tema.eixo }}
              axisLine={false}
              tickLine={false}
            />
            <YAxis
              type="category"
              dataKey="marca"
              width={84}
              tick={{ fontSize: 12, fill: tema.eixo }}
              axisLine={false}
              tickLine={false}
            />
            <Tooltip
              cursor={{ fill: tema.grade, opacity: 0.45 }}
              formatter={(v: unknown, nome: unknown) => [fmt(v), ROTULO_TAXA[nome as CanalDoGrafico] ?? String(nome)]}
              contentStyle={{
                background: tema.tooltipFundo,
                border: `1px solid ${tema.tooltipBorda}`,
                borderRadius: 8,
                fontSize: 12,
                color: tema.tooltipTinta,
              }}
              labelStyle={{ color: tema.tooltipTinta, fontWeight: 600 }}
            />
            {canais.map((c) => (
              <Bar animationDuration={450} animationEasing="ease-out" key={c} dataKey={c} fill={tema.canais[c]} radius={[0, 3, 3, 0]} maxBarSize={12} isAnimationActive={false}>
                <LabelList dataKey={c} position="right" formatter={fmt} style={{ fontSize: 12, fill: tema.eixo }} />
              </Bar>
            ))}
          </BarChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
