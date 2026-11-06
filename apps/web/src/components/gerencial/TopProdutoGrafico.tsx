"use client";

/**
 * Barras do detalhe de um produto do Top da Gerencial. Carregado por
 * `next/dynamic` (como o EvolutionChart) para o recharts ficar fora do bundle
 * inicial. Cor = identidade do canal; o periodo selecionado nao muda a cor.
 */
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { temaDoGrafico } from "@/lib/chart-theme";
import { useTema } from "@/components/theme/ThemeProvider";
import { fmtBrlCurto, fmtNumCurto } from "@/lib/formatters";
import type { Barra, CanalTop } from "@/lib/gerencial/top-produtos";

export default function TopProdutoGrafico({
  barras, metrica, canal,
}: { barras: Barra[]; metrica: "receita" | "pecas"; canal: CanalTop }) {
  const tema = temaDoGrafico(useTema().resolvido);
  const fmt = metrica === "receita" ? fmtBrlCurto : fmtNumCurto;
  return (
    <ResponsiveContainer width="100%" height="100%">
      <BarChart data={barras} margin={{ top: 8, right: 4, bottom: 0, left: 4 }}>
        <CartesianGrid strokeDasharray="3 3" stroke={tema.grade} vertical={false} />
        <XAxis dataKey="rotulo" tick={{ fontSize: 12, fill: tema.eixo }} axisLine={false} tickLine={false} minTickGap={12} />
        <YAxis tick={{ fontSize: 12, fill: tema.eixo }} axisLine={false} tickLine={false} width={56} tickFormatter={(v: number) => fmtNumCurto(v)} />
        <Tooltip
          cursor={{ fill: tema.grade, opacity: 0.5 }}
          contentStyle={{ background: tema.tooltipFundo, border: `1px solid ${tema.tooltipBorda}`, borderRadius: 8, color: tema.tooltipTinta, fontSize: 12 }}
          formatter={(v: number) => [fmt(v), metrica === "receita" ? "Receita" : "Peças"]}
        />
        <Bar dataKey={metrica} fill={tema.canais[canal]} radius={[4, 4, 0, 0]} maxBarSize={28} />
      </BarChart>
    </ResponsiveContainer>
  );
}
