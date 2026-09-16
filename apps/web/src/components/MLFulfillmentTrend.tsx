"use client";

import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { PontoSerie, MetricaSerie } from "@/lib/ml-fulfillment";
import { SEM_DADO, fmtShare } from "@/lib/ml-fulfillment";

const METRICA_LABEL: Record<MetricaSerie, string> = {
  gmv: "GMV",
  orders: "pedidos",
  units: "unidades",
};

interface Props {
  pontos: PontoSerie[];
  metrica: MetricaSerie;
}

function diaCurto(iso: string): string {
  const [, m, d] = iso.split("-");
  return `${d}/${m}`;
}

interface TooltipEntry {
  payload?: PontoSerie;
}

function CustomTooltip({
  active, payload, metrica,
}: { active?: boolean; payload?: TooltipEntry[]; metrica: MetricaSerie }) {
  if (!active || !payload?.length) return null;
  const p = payload[0]?.payload;
  if (!p) return null;
  return (
    <div className="bg-white border border-slate-200 rounded-lg shadow-lg px-3 py-2 text-xs">
      <p className="font-semibold text-slate-900">{p.ref_date}</p>
      <p className="text-slate-600 mt-1">
        Share Full ({METRICA_LABEL[metrica]}):{" "}
        <span className="font-semibold tabular-nums">
          {p.share_full === null ? SEM_DADO : fmtShare(p.share_full)}
        </span>
      </p>
      {p.share_full === null && (
        <p className="text-slate-500 mt-1">Sem venda classificada neste dia.</p>
      )}
    </div>
  );
}

/**
 * Tendencia diaria do share de Full.
 *
 * O grafico NAO e' a unica via: abaixo dele vive uma tabela com os mesmos
 * pontos, exposta por `<details>` e navegavel por teclado. Quem usa leitor de
 * tela, quem imprime e quem precisa conferir numero exato chega ao mesmo dado
 * sem depender do SVG.
 *
 * Dias com `share_full = null` (nenhuma venda classificada) ficam com furo na
 * linha em vez de cair a zero -- `connectNulls` desligado de proposito: ligar
 * os pontos inventaria uma queda que nao houve.
 */
export default function MLFulfillmentTrend({ pontos, metrica }: Props) {
  if (pontos.length === 0) {
    return (
      <p className="text-sm text-slate-500 py-8 text-center">
        Sem serie diaria para o periodo selecionado.
      </p>
    );
  }

  const comDado = pontos.filter((p) => p.share_full !== null);
  const media =
    comDado.length > 0
      ? comDado.reduce((a, p) => a + (p.share_full ?? 0), 0) / comDado.length
      : null;

  return (
    <div className="flex flex-col gap-3">
      <div className="h-[280px] w-full" role="img"
        aria-label={
          `Tendencia diaria do share de Full por ${METRICA_LABEL[metrica]}, ` +
          `${pontos.length} dias, media de ${media === null ? SEM_DADO : fmtShare(media)}. ` +
          "Os valores exatos estao na tabela logo abaixo do grafico."
        }>
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={pontos} margin={{ top: 8, right: 8, bottom: 4, left: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
            <XAxis
              dataKey="ref_date"
              tickFormatter={diaCurto}
              tick={{ fontSize: 12, fill: "#64748b" }}
              minTickGap={24}
            />
            <YAxis
              domain={[0, 1]}
              tickFormatter={(v: number) => `${Math.round(v * 100)}%`}
              tick={{ fontSize: 12, fill: "#64748b" }}
              width={48}
            />
            <Tooltip content={<CustomTooltip metrica={metrica} />} />
            <Line
              type="monotone"
              dataKey="share_full"
              stroke="#7c3aed"
              strokeWidth={2}
              dot={false}
              connectNulls={false}
              name="Share Full"
            />
          </LineChart>
        </ResponsiveContainer>
      </div>

      <details className="group">
        <summary className="cursor-pointer text-xs font-medium text-slate-600 hover:text-slate-900 focus-visible:outline focus-visible:outline-2 focus-visible:outline-violet-600 rounded min-h-11 flex items-center">
          Ver os valores do gráfico em tabela ({pontos.length} dias)
        </summary>
        <div className="mt-2 max-h-72 overflow-y-auto overflow-x-auto border border-slate-200 rounded-lg">
          <table className="w-full text-xs">
            <caption className="sr-only">
              Share diário de Full por {METRICA_LABEL[metrica]}
            </caption>
            <thead className="bg-slate-50 sticky top-0">
              <tr>
                <th scope="col" className="text-left px-3 py-2 font-semibold text-slate-700">Data</th>
                <th scope="col" className="text-right px-3 py-2 font-semibold text-slate-700">Share Full</th>
                <th scope="col" className="text-right px-3 py-2 font-semibold text-slate-700">Full</th>
                <th scope="col" className="text-right px-3 py-2 font-semibold text-slate-700">Não-Full</th>
              </tr>
            </thead>
            <tbody>
              {pontos.map((p) => (
                <tr key={p.ref_date} className="border-t border-slate-100">
                  <td className="px-3 py-1.5 text-slate-700">{p.ref_date}</td>
                  <td className="px-3 py-1.5 text-right tabular-nums font-medium">
                    {p.share_full === null ? SEM_DADO : fmtShare(p.share_full)}
                  </td>
                  <td className="px-3 py-1.5 text-right tabular-nums text-slate-600">
                    {metrica === "gmv" ? p.full.toFixed(2) : p.full.toLocaleString("pt-BR")}
                  </td>
                  <td className="px-3 py-1.5 text-right tabular-nums text-slate-600">
                    {metrica === "gmv" ? p.non_full.toFixed(2) : p.non_full.toLocaleString("pt-BR")}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
    </div>
  );
}
