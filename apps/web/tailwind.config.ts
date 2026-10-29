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
        // Identidade de canal (UX-TORRE-2): cor por CANAL, nunca por posicao.
        // VISUAL-PALETA-1 — identidade de cada KPI (ver globals.css).
        kpi: {
          gmv: token("kpi-gmv"),
          pedidos: token("kpi-pedidos"),
          ticket: token("kpi-ticket"),
          ads: token("kpi-ads"),
          roas: token("kpi-roas"),
        },
        canal: {
          tiktok: token("canal-tiktok"),
          ml: token("canal-ml"),
          shopee: token("canal-shopee"),
        },
        // UX-TORRE-B2B-1 — moldura navy do shell, escura nos dois temas.
        shell: {
          DEFAULT: token("shell"),
          raised: token("shell-raised"),
          line: token("shell-line"),
          ink: token("shell-ink"),
          muted: token("shell-muted"),
          faint: token("shell-faint"),
          accent: token("shell-accent"),
          active: token("shell-active"),
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
      // UX-TORRE-B2B-1 — o degrau `data` do DESIGN.md (13px / 1.35) como
      // papel nomeado: celula de tabela densa e item de navegacao.
      fontSize: {
        data: ["13px", { lineHeight: "1.35" }],
      },
      fontFamily: {
        sans: [
          "var(--font-sans)",
          "-apple-system",
          "BlinkMacSystemFont",
          '"Segoe UI"',
          "Roboto",
          "sans-serif",
        ],
      },
    },
  },
  plugins: [],
};

export default config;
