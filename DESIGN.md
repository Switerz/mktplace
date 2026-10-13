---
name: Torre de Controle — GoBeauté
description: Internal marketplace control tower for the GoBeauté brands across TikTok Shop, Mercado Livre and Shopee.
colors:
  shell: "#12112b"
  shell-raised: "#1e1c40"
  shell-line: "#28254e"
  shell-ink: "#f0eefc"
  shell-muted: "#aaa6ce"
  shell-accent: "#a78bfa"
  canvas: "#f7f6fb"
  surface: "#ffffff"
  raised: "#f4f2fb"
  line: "#e7e3f5"
  line-strong: "#d6d0ec"
  ink: "#171226"
  ink-muted: "#524c67"
  ink-faint: "#6c6582"
  accent: "#6d28d9"
  accent-soft: "#f1ecfe"
  ok: "#059669"
  warn: "#d97706"
  crit: "#e11d48"
  canal-tiktok: "#8b5cf6"
  canal-ml: "#06b6d4"
  canal-shopee: "#f97316"
typography:
  label:
    fontFamily: "Inter, -apple-system, 'Segoe UI', sans-serif"
    fontSize: "12px"
    fontWeight: 600
    lineHeight: 1.2
    letterSpacing: "0.04em"
  data:
    fontFamily: "Inter, -apple-system, 'Segoe UI', sans-serif"
    fontSize: "13px"
    fontWeight: 400
    lineHeight: 1.35
    letterSpacing: "normal"
  body:
    fontFamily: "Inter, -apple-system, 'Segoe UI', sans-serif"
    fontSize: "14px"
    fontWeight: 400
    lineHeight: 1.5
    letterSpacing: "normal"
  title:
    fontFamily: "Inter, -apple-system, 'Segoe UI', sans-serif"
    fontSize: "14px"
    fontWeight: 600
    lineHeight: 1.4
    letterSpacing: "normal"
  headline:
    fontFamily: "Inter, -apple-system, 'Segoe UI', sans-serif"
    fontSize: "20px"
    fontWeight: 700
    lineHeight: 1.3
    letterSpacing: "-0.01em"
  kpi:
    fontFamily: "Inter, -apple-system, 'Segoe UI', sans-serif"
    fontSize: "24px"
    fontWeight: 600
    lineHeight: 1.15
    letterSpacing: "-0.015em"
  kpi-hero:
    fontFamily: "Inter, -apple-system, 'Segoe UI', sans-serif"
    fontSize: "28px"
    fontWeight: 600
    lineHeight: 1
    letterSpacing: "-0.02em"
rounded:
  control: "8px"
  panel: "12px"
  full: "9999px"
spacing:
  xs: "6px"
  sm: "12px"
  md: "16px"
  lg: "24px"
components:
  sidebar:
    backgroundColor: "{colors.shell}"
    textColor: "{colors.shell-muted}"
    width: "240px expanded / 64px rail"
  nav-item-active:
    backgroundColor: "#2c2260"
    textColor: "{colors.shell-ink}"
    indicator: "{colors.shell-accent}"
  panel:
    backgroundColor: "{colors.surface}"
    rounded: "{rounded.panel}"
    border: "{colors.line}"
  kpi-card:
    backgroundColor: "{colors.surface}"
    rounded: "{rounded.panel}"
    padding: "16px"
  filter-trigger:
    backgroundColor: "{colors.surface}"
    rounded: "{rounded.control}"
    height: "44px"
---

# Design System: Torre de Controle — GoBeauté

> Revised in **UX-TORRE-B2B-1** (13/10/2026). Mário set the private repository
> `b2b-gogroup/torre_b2b` as the explicit finish benchmark and approved a
> **navy operational shell** around a light data canvas. That decision replaces
> the old rule "never a dark sidebar". The benchmark audit lives in
> `docs/ux_torre_b2b_benchmark.md`.

## 1. Overview

**Creative North Star: "The instrument panel"**

The Torre reads like a flight deck, not a report. A dark navy frame holds the
navigation, identity and theme. Inside it sits a bright, dense data canvas: KPI
strip first, then wide panels with charts and tables, and depth one click away.
The category default is a stack of filter blocks, paragraphs and same-size
cards. The Torre refuses it: every viewport has to answer a question with
numbers.

What changed from the previous "Brand Observatory" direction, and why:

| Before | Now | Why |
|---|---|---|
| Light lavender sidebar | Navy shell (`--tc-shell`) in **both** themes | Mário's brief; the frame separates navigation from data at a glance |
| System font stack | **Inter** via `next/font` (self-hosted) | True tabular figures; consistent rendering across Windows/macOS |
| 16px cards, shadow at rest | 12px panels, 1px border, shadow only on hover | Denser, quieter; the border defines the edge |
| Two-row filter bar | One row: channel segmented + brand/period triggers | First number moved up ~60px |
| Prose under titles | Chips (period, freshness) + short notes + disclosures | Warnings stay; their weight drops |

The four anti-references still hold:

- **Metabase.** The charcoal sidebar with an electric-blue accent is still
  forbidden. Navy with a violet cast and violet selection is not that.
- **Looker data dump.** Still forbidden.
- **Retool gray.** Still forbidden.
- **Excel beige.** Still forbidden.

**Key characteristics**

- Navy shell, light canvas, white panels, violet for selection and primary series only
- Inter, tabular numerals everywhere numbers live
- One icon vocabulary (`lucide-react`, 16px, stroke 1.75); the route icon repeats in the page header
- KPIs as a strip of compact cards: label, icon, number, delta pill, short note
- Status colours only behind a business rule; channel colours only for identity

## 2. Colors

### Shell (new)

`--tc-shell` is navy with a violet cast (`#12112b` light, `#0a0914` dark, one
step below the dark canvas). Text on it uses `shell-ink` (16.1:1),
`shell-muted` (7.9:1) and `shell-faint` (5.1:1, group labels only) — measured; the dark shell measures 17.4 / 8.8 / 5.5. The active
nav item is marked by three signals together: `shell-active` surface,
`shell-ink` text, and a 3px `shell-accent` bar on the left edge. Weight alone is
never the signal.

### Canvas and surfaces

The light canvas is lavender-white (`#f7f6fb`). Panels are white with a 1px
`line` border. `raised` is the inset tone used for table heads, totals and
skeletons. The dark theme keeps its deep violet field. See §7 for every value.

### Violet discipline

Violet (`accent`) is allowed on:

- the active nav item;
- the brand mark;
- the route icon tile in the page header;
- the first data series;
- primary actions and links.

It is **not** a button fill for filter options. Active filter options use
neutral relief (`bg-surface` + `ring-line-strong`).

### Channel identity

TikTok is violet, ML is cyan, Shopee is orange. This holds everywhere: the
filter dots, charts, matrix columns and table badges. It is identity, never
quality.

### Status

`ok` / `warn` / `crit` each need a business threshold behind them: ROAS bands,
fee bands, buyer-profile bands, data-confidence severity. Use full tones for
fills and dots, and the `-ink` variants for text.

### Brand avatars

The brand palette (BARBOURS violet, KOKESHI cyan, ÁPICE amber, LESCENT pink,
RITUÁRIA emerald) is reserved for brand identity marks. It is not used for chart
series or status.

## 3. Typography

**Family:** Inter (`next/font/google`, self-hosted at build, `display: swap`),
falling back to the system stack. It was chosen for real `tnum` figures, so R$
columns align without monospace.

| Role | Size / weight | Use |
|---|---|---|
| label | 12px / 600, uppercase, tracked 0.04em | KPI labels, table column heads, nav group labels, status badges |
| data | 13px / 400–600 | Dense table cells, nav items |
| body | 14px / 400 | Prose, notes, panel subtitles |
| title | 14px / 600 | Panel titles |
| headline | 20px / 700 | Page title (`<h2>`; Gerencial keeps its `<h1>`) |
| kpi | 24px / 600, −0.015em | KPI card value |
| kpi-hero | 28px / 600 | `KpiTile destaque` only |

**Floor: 12px.** The benchmark uses 10–11px labels everywhere. The Torre does
not copy that: the 12px floor was a deliberate UX-TORRE-1 accessibility
decision, and density comes from spacing, not from smaller type.

**Uppercase labels** belong to data infrastructure: KPI labels, column heads,
nav group labels and badges. Panel titles are mixed-case title weight.

**Tabular numerals** are on for every table and every KPI value
(`tabular-nums`, or the global `table, .tabular` rule).

## 4. Elevation

The system is tonal first. Depth comes from canvas → surface → raised, then the
1px border. Shadow appears only:

- on hover of an interactive card or row: a soft offset with a violet cast,
  `0 4px 14px -4px rgb(accent / .18)`;
- on floating layers (popovers and dialogs): `0 8px 24px -6px rgb(ink / .18)`.

Static panels never cast a shadow.

## 5. Components

The primitives live in `apps/web/src/components/ui/`, plus `layout/` and
`shell/`. A primitive exists only when it has two or more consumers.

| Primitive | File | Consumers |
|---|---|---|
| App shell (navy sidebar, mobile topbar, drawer) | `shell/*` | every route |
| Page header (icon, title, subtitle, meta chips, sticky filter bar) | `layout/PageHeader.tsx`, `gerencial/GerencialHeader.tsx` | 10 routes + Gerencial |
| Filter toolbar | `layout/GlobalFilterControls.tsx` | Gerencial, Canais, Financeiro |
| Brand selector / period selector, `menu` variant | `BrandFilter.tsx`, `DateRangeFilter.tsx` + `ui/Popover.tsx` | toolbar (menu); other routes (faixa) |
| Freshness + period chip | `ui/Freshness.tsx` | Gerencial, Canais, Financeiro |
| KPI card | `ui/KpiTile.tsx`, `ui/StatCard.tsx`, `gerencial/KpiBand.tsx` | Expedição, Canais, Financeiro, Gerencial |
| Panel (title, subtitle, actions, footer, icon) | `ui/Panel.tsx` | Expedição, Canais, Financeiro, Gerencial |
| Segmented / tabs | `ui/Segmented.tsx` | theme, Expedição, Canais detail tabs |
| Status badge | `ui/Chip.tsx` | Expedição, Canais, Financeiro |
| Data-quality note + disclosure | `ui/Note.tsx`, `ui/Disclosure.tsx` | all three + Expedição |
| Table shell (scroll hint) | `TableScrollHint.tsx` | all wide tables |
| Empty / loading / error / unavailable | `ui/StateBlock.tsx` | Canais, Financeiro |

### Sidebar

- 240px expanded, 64px icon rail.
- **Auto mode** shows the rail below 1280px and the expanded bar above. Only a
  choice made on a wide screen persists (`localStorage: torre-sidebar`).
- The theme selector lives in the sidebar footer: segmented when expanded, one
  cycling button in the rail.
- Mobile uses a navy topbar with a hamburger that opens a navy drawer with
  44px targets.

### Filter toolbar

- One row on desktop, sticky.
- Channel is a segmented control with identity dots.
- Brand and period are popover triggers showing the current value ("Marca:
  Todas", "Período: Mês anterior").
- Every target is 44px; that contract is V3 and is not traded for density.
- Below `sm` the bar collapses behind a "Filtros" button that shows the scope
  summary.

### KPI card

- Uppercase label at the top left and a 28px icon tile at the top right (series
  tint, not status).
- 24px value, then a delta pill (`ok-soft` / `crit-soft` with an arrow), then
  the reference.
- Notes sit at the base in 12px muted text. A note is never removed, only
  shortened.

### Panel

- 12px radius, 1px border.
- Header: optional 16px icon, title (14/600), one-line subtitle, actions on the
  right.
- Methodology longer than one line goes into a `Disclosure` ("Como ler esta
  medição").

### Tables

- Head on `raised`, using the label style.
- Rows are 13–14px with no zebra striping and a hover tint.
- Numbers are right-aligned and tabular.
- The total row sits on `raised` with a 2px top border.
- Wide tables scroll inside the panel, with a visible hint.

### Charts

- Recharts.
- Grid from `--tc-chart-grid`, 12px axis ticks, no axis lines.
- Series colour comes from position or channel identity, never status.
- Every chart has a title and the period in its header.

## 6. Do's and Don'ts

**Do**

- Put a number in the first viewport of every route, above any prose.
- Turn metadata sentences into chips: period, freshness, coverage.
- Keep quality warnings visible and lighter: a short note, with the full text
  in a disclosure or a `title`.
- Repeat the route icon in the page header.
- Keep the 44px target on every filter control and the 12px type floor.

**Don't**

- Use a charcoal/grey sidebar with an electric-blue accent (the Metabase
  default). The shell is navy-violet.
- Fill filter options with solid violet.
- Use status colours without a business rule, or channel colours for anything
  except identity.
- Put a shadow on static panels, use a coloured side stripe on cards or alerts,
  or use gradient text.
- Call any cost view "margem real". Known costs are labelled as known costs,
  and missing components are named as missing.

---

## 7. Semantic tokens and the two themes (UX-TORRE-1, extended)

No screen writes a hex or a palette literal. Each colour is a **role**, and the
role's value is defined once per theme in `apps/web/app/globals.css`.

### The token layer

| Role | Token | Light | Dark |
|---|---|---|---|
| Page field | `--tc-canvas` | `#f7f6fb` | `#0e0b18` |
| Card | `--tc-surface` | `#ffffff` | `#171327` |
| Elevated / inset | `--tc-raised` | `#f4f2fb` | `#1f1a33` |
| Border | `--tc-line` / `--tc-line-strong` | `#e7e3f5` / `#d6d0ec` | `#2c2544` / `#3d3459` |
| Text | `--tc-ink` / `--tc-muted` / `--tc-faint` | `#171226` / `#524c67` / `#6c6582` | `#f2effa` / `#b8b0d0` / `#968db2` |
| Accent | `--tc-accent` (+ `-soft`, `-ink`, `-on`) | `#6d28d9` | `#a78bfa` |
| Success / Warn / Critical | `--tc-ok` / `--tc-warn` / `--tc-crit` (+ `-soft`, `-ink`) | `#059669` / `#d97706` / `#e11d48` | `#34d399` / `#fbbf24` / `#fb7185` |
| Series 1–6 | `--tc-chart-1…6` | violet, sky, slate, purple, cyan, slate-dark | the lighter counterpart of each |
| Channel identity (UX-TORRE-2) | `--tc-canal-tiktok` / `-ml` / `-shopee` | `#8b5cf6` / `#06b6d4` / `#f97316` | `#a78bfa` / `#22d3ee` / `#fb923c` |
| Shell (UX-TORRE-B2B-1) | `--tc-shell` (+ `-raised`, `-line`, `-ink`, `-muted`, `-faint`, `-accent`, `-active`) | `#12112b` | `#0a0914` |

Tokens are stored as **RGB channels** (`23 18 38`), never `#rrggbb`. Tailwind
injects the alpha inside `rgb(var(--tc-ink) / <alpha-value>)`, which is why
`text-ink/70` works.

The tokens enforce three rules:

1. **Dark is not an inversion.** Each role has its own value per theme,
   checked for ≥4.5:1 body text and ≥3:1 large text and UI.
2. **Full `ok`/`warn`/`crit` are fills.** Text uses the `-ink` variant.
3. **`-on` is the ink that sits on the full accent.** It flips in dark.

### Theme switching

- `darkMode: "class"`.
- Three states (`light`, `dark`, `system`), persisted under `torre-tema`.
- A synchronous `<head>` script applies the theme before first paint.

### Migration barrier

Routes in `ROTAS_TEMATIZADAS` (`src/lib/theme.ts`) are on tokens. Every other
route renders under `.tc-tema-claro`. The shell is themed on every route; a
route's content joins the dark theme only when its PR adds it to the list.
