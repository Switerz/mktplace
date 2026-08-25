"use client";

import { useId } from "react";
import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { DailyRow } from "@/lib/mock-daily";
import { fmtBrl } from "@/lib/formatters";

interface Props {
  data: DailyRow[];
  hasTiktok: boolean;
  hasMl: boolean;
  hasShopee: boolean;
}

const SERIES_LABEL: Record<string, string> = {
  tiktok: "TikTok",
  ml: "Mercado Livre",
  shopee: "Shopee",
  total: "Total",
};

/** Piso tipografico do V3: nenhum glifo renderizado abaixo de 12px. Os rotulos
 * de eixo do Recharts eram os ultimos a 10px em toda a interface. */
const TICK = { fontSize: 12, fill: "#64748b" } as const;

function shortDate(dateStr: string): string {
  const d = new Date(dateStr + "T00:00:00");
  return `${d.getDate()}/${d.getMonth() + 1}`;
}

export default function DailyChart({ data, hasTiktok, hasMl, hasShopee }: Props) {
  const descId = useId();
  const chartData = data.map((r) => ({
    date: shortDate(r.date),
    tiktok: r.tiktok_gmv ?? undefined,
    ml: r.ml_gmv ?? undefined,
    shopee: r.shopee_gmv ?? undefined,
    total: r.total_gmv,
  }));

  // Renderiza uma serie por canal ativo (nunca uma linha "Total" ambigua que
  // misture canais sem identifica-los); se nenhum canal individual tiver
  // dado, cai para "total" como ultimo recurso.
  const activeSeries: { key: "tiktok" | "ml" | "shopee"; color: string; gradientId: string }[] = [];
  if (hasTiktok) activeSeries.push({ key: "tiktok", color: "#7c3aed", gradientId: "gradTk" });
  if (hasMl) activeSeries.push({ key: "ml", color: "#f59e0b", gradientId: "gradMl" });
  if (hasShopee) activeSeries.push({ key: "shopee", color: "#f97316", gradientId: "gradSh" });

  /**
   * Legenda e descricao anunciam SOMENTE o que esta plotado. Quando nenhum
   * canal tem dado, o que existe na tela e' a serie combinada, e e' ela que a
   * legenda nomeia — nunca tres canais que nao estao ali.
   */
  const legenda = activeSeries.length > 0
    ? activeSeries.map((s) => ({ nome: SERIES_LABEL[s.key], cor: s.color }))
    : [{ nome: SERIES_LABEL.total, cor: "#7c3aed" }];

  const primeiro = chartData[0]?.date;
  const ultimo = chartData[chartData.length - 1]?.date;
  const intervalo = chartData.length === 0
    ? "sem nenhum dia no intervalo"
    : chartData.length === 1
      ? `um único dia, ${primeiro}`
      : `de ${primeiro} a ${ultimo}, ${chartData.length} dias`;
  const nomeAcessivel = `Gráfico de GMV diário — ${intervalo}`;
  const descricao =
    `GMV diário por canal, ${intervalo}. Séries exibidas: ${legenda.map((l) => l.nome).join(", ")}. ` +
    "Dia sem cobertura no canal fica sem ponto na série, e não é desenhado como zero; " +
    "zero medido é desenhado na linha de base.";

  return (
    <div className="bg-white rounded-2xl shadow-sm border border-violet-100 p-5">
      <h2 className="text-sm font-semibold text-slate-700 mb-4">
        GMV Diário — Últimos 60 dias
      </h2>

      {/* Descricao textual. Fica FORA do `role="img"` de proposito: dentro dele
          o conteudo seria apresentacional e nunca chegaria ao leitor de tela. */}
      <p id={descId} className="sr-only">{descricao}</p>

      {/*
        `role="img"` + `aria-label` dao ao grafico UMA representacao acessivel,
        com nome proprio. Por especificacao a subarvore de um `role="img"` e'
        apresentacional, entao o `<svg>` do Recharts nao gera um segundo
        anuncio: nao ha duplicidade, e nao foi preciso `aria-hidden` nele nem
        `suppressHydrationWarning`.
      */}
      <div role="img" aria-label={nomeAcessivel} aria-describedby={descId}>
        <ResponsiveContainer width="100%" height={220}>
          <AreaChart data={chartData} margin={{ top: 4, right: 0, left: 0, bottom: 0 }}>
            <defs>
              <linearGradient id="gradTk" x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%" stopColor="#7c3aed" stopOpacity={0.15} />
                <stop offset="95%" stopColor="#7c3aed" stopOpacity={0} />
              </linearGradient>
              <linearGradient id="gradMl" x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%" stopColor="#f59e0b" stopOpacity={0.15} />
                <stop offset="95%" stopColor="#f59e0b" stopOpacity={0} />
              </linearGradient>
              <linearGradient id="gradSh" x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%" stopColor="#f97316" stopOpacity={0.15} />
                <stop offset="95%" stopColor="#f97316" stopOpacity={0} />
              </linearGradient>
            </defs>
            <CartesianGrid strokeDasharray="3 3" stroke="#f3f0ff" vertical={false} />
            <XAxis
              dataKey="date"
              tick={TICK}
              axisLine={false}
              tickLine={false}
              // `minTickGap` no lugar de um `interval` fixo de 6: a 12px o rotulo
              // e' mais largo e o passo fixo colidia no mobile. Assim o Recharts
              // rareia conforme a largura, preservando o primeiro e o ultimo dia
              // — nenhum tick importante e' descartado para "fazer caber".
              interval="preserveStartEnd"
              minTickGap={28}
            />
            <YAxis
              tickFormatter={(v) => fmtBrl(v)}
              tick={TICK}
              axisLine={false}
              tickLine={false}
              // 68px cabia "R$ 180K" a 10px; a 12px o mesmo rotulo encostava na
              // borda. 80px preserva o valor monetario inteiro, sem corte.
              width={80}
            />
            <Tooltip
              formatter={(value: number, name: string) => [fmtBrl(value), SERIES_LABEL[name] ?? name]}
              contentStyle={{ borderRadius: 12, border: "1px solid #ede9fe", fontSize: 12 }}
            />
            {activeSeries.length > 0 ? (
              activeSeries.map((s) => (
                <Area
                  key={s.key}
                  type="monotone"
                  dataKey={s.key}
                  stroke={s.color}
                  strokeWidth={2}
                  fill={`url(#${s.gradientId})`}
                  dot={false}
                />
              ))
            ) : (
              <Area type="monotone" dataKey="total" stroke="#7c3aed" strokeWidth={2} fill="url(#gradTk)" dot={false} />
            )}
          </AreaChart>
        </ResponsiveContainer>
      </div>

      {/*
        Legenda propria, no lugar do `<Legend>` do Recharts, por dois motivos que
        o componente automatico nao garantia: ela precisa ficar FORA do
        `role="img"` para ser anunciada como texto, e o marcador colorido precisa
        ser explicitamente decorativo. A cor deixa de ser o unico portador — o
        nome da serie esta em texto ao lado, e so' series ativas entram.
      */}
      <ul
        aria-label="Séries do gráfico"
        className="flex flex-wrap justify-center gap-x-5 gap-y-1 list-none m-0 p-0 mt-2"
      >
        {legenda.map((l) => (
          <li key={l.nome} className="flex items-center gap-1.5 text-xs text-slate-600">
            <span
              aria-hidden="true"
              className="w-3 h-0.5 rounded-full shrink-0"
              style={{ backgroundColor: l.cor }}
            />
            {l.nome}
          </li>
        ))}
      </ul>
    </div>
  );
}
