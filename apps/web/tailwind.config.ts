import type { Config } from "tailwindcss";

/** `rgb(var(--x) / <alpha-value>)` e' o que preserva `bg-surface/60`. */
const token = (nome: string) => `rgb(var(--tc-${nome}) / <alpha-value>)`;

const config: Config = {
  // UX-TORRE-1: tema por CLASSE, nunca por media query. A preferencia do
  // sistema e' so' o valor INICIAL; a escolha manual precisa sobrepor o SO, e
  // `darkMode: "media"` tornaria isso impossivel.
  darkMode: "class",
  content: ["./src/**/*.{ts,tsx}", "./app/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // --- tokens semanticos (ver app/globals.css) ---
        canvas: token("canvas"),
        surface: token("surface"),
        raised: token("raised"),
        line: {
          DEFAULT: token("line"),
          strong: token("line-strong"),
        },
        ink: {
          DEFAULT: token("ink"),
          muted: token("muted"),
          faint: token("faint"),
        },
        accent: {
          DEFAULT: token("accent"),
          soft: token("accent-soft"),
          ink: token("accent-ink"),
          on: token("accent-on"),
        },
        ok: {
          DEFAULT: token("ok"),
          soft: token("ok-soft"),
          ink: token("ok-ink"),
        },
        warn: {
          DEFAULT: token("warn"),
          soft: token("warn-soft"),
          ink: token("warn-ink"),
        },
        crit: {
          DEFAULT: token("crit"),
          soft: token("crit-soft"),
          ink: token("crit-ink"),
        },
        // Paleta de series: cor por POSICAO na lista, nunca por estado.
        chart: {
          1: token("chart-1"),
          2: token("chart-2"),
          3: token("chart-3"),
          4: token("chart-4"),
          5: token("chart-5"),
          6: token("chart-6"),
        },
        // Escala literal herdada, preservada para as rotas ainda nao migradas.
        brand: {
          50: "#f5f3ff",
          100: "#ede9fe",
          600: "#7c3aed",
          700: "#6d28d9",
          900: "#2e1065",
        },
      },
    },
  },
  plugins: [],
};

export default config;
