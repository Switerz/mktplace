# Product

## Register

product

## Users

Mixed audience — two daily modes of use:

- **Ops analysts** open the dashboard every morning to verify if numbers are on-track, spot anomalies (data gaps, GMV drops, goal misses), and escalate or act. They need dense, fast, reliable data without hunting.
- **Brand managers** check in a few times per week to assess brand performance, compare channels, and make budget or allocation decisions. They need the executive summary first, with depth one click away.

Language: Portuguese (BR). Context: internal GoBeauté office environment.

## Product Purpose

Internal command center for GoBeauté's marketplace operations. Monitors GMV, orders, average ticket, ad spend, and goal attainment across five brands (ÁPICE, BARBOURS, KOKESHI, LESCENT, RITUÁRIA) on TikTok Shop and Mercado Livre.

Real-time data from the GoBeauté Data Mart (PostgreSQL via Metabase API) with graceful mock-data fallback. Period selector (monthly), marketplace filter, brand drill-down pages, and goal attainment vs XLSX targets.

Success means: any team member can open the dashboard, assess the entire portfolio's health, and know what needs attention in under 30 seconds.

## Brand Personality

**Elegante, profissional, moderno.** GoBeauté's consumer brand quality and refinement belongs in the internal tool too — not just in the storefront. The tool should feel like something the company is proud of, not a hastily-built spreadsheet replacement.

Voice: calm authority. Data presented with confidence, not apology. Numbers are large and legible. Status signals are instant.

## Anti-references

- **Metabase / generic SaaS default** — charcoal sidebar, electric blue accent, gray card grids with no visual identity. Looks like every BI tool ever made.
- **Google Data Studio / Looker** — data dump aesthetic. Oversized chart blocks, no hierarchy, information density without clarity.
- **Retool** — dense gray form-builder look. Ugly-by-design "internal tool" aesthetic.
- **Corporate Excel dashboard** — beige backgrounds, 3D pie charts, thick borders, no typographic hierarchy.

## Design Principles

1. **Brand quality, inside out.** Internal tools aren't second-class citizens. GoBeauté's elegance — refined palette, generous spacing, confident typography — belongs here as much as on the storefront.
2. **Status at a glance.** Goal attainment colors (green / amber / red), MoM arrows, and live/demo badges must be readable in peripheral vision. Never bury the lead.
3. **Summary first, depth on demand.** The KPI row answers the manager's question; the brand table and drill-down answer the analyst's. No forced toggling between modes.
4. **Data with no decoration tax.** Every visual element must either carry information or direct attention. No decorative borders, gradient fills, or icon collections that don't map to meaning.
5. **Ops confidence through precision.** Monospace numerics, tight tabular alignment, consistent date/period labeling. A number that shifts layout is a number that erodes trust.

## Accessibility & Inclusion

WCAG AA standard: ≥4.5:1 contrast for normal text, ≥3:1 for large text and UI components. Internal tool audience, Portuguese BR, no specific accommodation requirements beyond standard AA. Reduced motion support expected.
