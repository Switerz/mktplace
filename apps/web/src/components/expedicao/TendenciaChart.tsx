"use client";

/**
 * Gate EXP-UX-1 — evolucao do backlog por conta.
 * Retematizado no UX-TORRE-1.
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
 *
 * TEMA: o recharts pinta SVG, e `stroke`/`fill` precisam de uma cor resolvida.
 * Entao a paleta vem de `temaDoGrafico(...)` — o tema nao muda o DADO, so' o
 * valor de cada papel. Grade, eixos, tooltip e legenda trocam junto; um
 * tooltip branco sobre fundo escuro era o unico jeito de perder a leitura.
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

import { useTema } from "@/components/theme/ThemeProvider";
import { corDaSerie, temaDoGrafico } from "@/lib/chart-theme";
import type { PontoGrafico } from "@/lib/expedicao-contract";

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
  const { resolvido } = useTema();
  const tema = temaDoGrafico(resolvido);

  return (
    <div className="h-[280px] w-full">
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={pontos} margin={{ top: 8, right: 12, bottom: 4, left: -12 }}>
          <defs>
            {contas.map((c, i) => (
              <linearGradient key={c} id={`g-${i}`} x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor={corDaSerie(tema, i)} stopOpacity={0.24} />
                <stop offset="100%" stopColor={corDaSerie(tema, i)} stopOpacity={0.02} />
              </linearGradient>
            ))}
          </defs>
          <CartesianGrid stroke={tema.grade} vertical={false} />
          <XAxis
            dataKey="hora"
            tickFormatter={horaCurta}
            tick={{ fontSize: 12, fill: tema.eixo }}
            tickLine={false}
            axisLine={{ stroke: tema.grade }}
            minTickGap={28}
          />
          <YAxis
            tick={{ fontSize: 12, fill: tema.eixo }}
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
            cursor={{ stroke: tema.grade }}
            contentStyle={{
              borderRadius: 8,
              border: `1px solid ${tema.tooltipBorda}`,
              backgroundColor: tema.tooltipFundo,
              color: tema.tooltipTinta,
              fontSize: 12,
            }}
            labelStyle={{ color: tema.tooltipTinta }}
            itemStyle={{ color: tema.tooltipTinta }}
          />
          <Legend wrapperStyle={{ fontSize: 12, color: tema.eixo }} />
          {contas.map((c, i) => (
            <Area
              key={c}
              type="monotone"
              dataKey={c}
              name={c}
              stroke={corDaSerie(tema, i)}
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
