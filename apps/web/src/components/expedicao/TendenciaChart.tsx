"use client";

/**
 * Gate EXP-UX-1 — evolucao do backlog por conta.
 *
 * Carregado por `next/dynamic({ ssr: false })` para manter o recharts fora do
 * bundle inicial da Expedicao, como as demais telas da Torre ja fazem.
 *
 * UMA LINHA POR CONTA, nunca a soma. O mesmo pedido continua no backlog de uma
 * hora para a outra: somar contas ou horas o contaria duas vezes.
 *
 * `connectNulls={false}` e' deliberado. Hora sem ponto para uma conta e'
 * LACUNA, e a linha se interrompe. Costurar o buraco desenharia uma queda de
 * backlog que nunca aconteceu — exatamente o engano que o aviso de serie
 * parcial existe para impedir.
 */
import {
  Area,
  AreaChart,
  CartesianGrid,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import type { PontoGrafico } from "@/lib/expedicao-contract";

/**
 * Paleta por posicao, dentro do sistema da Torre: violeta e' a cor da marca e
 * abre a lista. Verde, ambar e vermelho ficam RESERVADOS para estado, entao
 * nao entram aqui — uma conta nao e' um estado.
 */
const CORES = ["#7c3aed", "#0ea5e9", "#64748b", "#a855f7", "#0891b2", "#475569"];

function horaCurta(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString("pt-BR", {
    day: "2-digit",
    month: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export default function TendenciaChart({
  pontos,
  contas,
}: {
  pontos: PontoGrafico[];
  contas: string[];
}) {
  return (
    <div className="h-[260px] w-full">
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={pontos} margin={{ top: 8, right: 12, bottom: 4, left: -12 }}>
          <defs>
            {contas.map((c, i) => (
              <linearGradient key={c} id={`g-${i}`} x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor={CORES[i % CORES.length]} stopOpacity={0.22} />
                <stop offset="100%" stopColor={CORES[i % CORES.length]} stopOpacity={0.02} />
              </linearGradient>
            ))}
          </defs>
          <CartesianGrid stroke="#ede9fe" vertical={false} />
          <XAxis
            dataKey="hora"
            tickFormatter={horaCurta}
            tick={{ fontSize: 12, fill: "#64748b" }}
            tickLine={false}
            axisLine={{ stroke: "#e2e8f0" }}
            minTickGap={28}
          />
          <YAxis
            tick={{ fontSize: 12, fill: "#64748b" }}
            tickLine={false}
            axisLine={false}
            width={48}
            allowDecimals={false}
          />
          <Tooltip
            labelFormatter={(v) => horaCurta(String(v))}
            formatter={(valor: number | string, nome: string) => [
              typeof valor === "number" ? valor.toLocaleString("pt-BR") : "sem dado",
              nome,
            ]}
            contentStyle={{
              borderRadius: 8,
              border: "1px solid #e2e8f0",
              fontSize: 12,
            }}
          />
          <Legend wrapperStyle={{ fontSize: 12 }} />
          {contas.map((c, i) => (
            <Area
              key={c}
              type="monotone"
              dataKey={c}
              name={c}
              stroke={CORES[i % CORES.length]}
              strokeWidth={2}
              fill={`url(#g-${i})`}
              connectNulls={false}
              dot={false}
              isAnimationActive={false}
            />
          ))}
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}
