---
name: Torre de Controle — GoBeauté
description: Internal marketplace intelligence dashboard for five beauty brands across TikTok Shop and Mercado Livre.
colors:
  primary: "#7c3aed"
  primary-deep: "#6d28d9"
  primary-darkest: "#2e1065"
  surface-bg: "#f8f7ff"
  surface-card: "#ffffff"
  surface-hover: "#f5f3ff"
  border-subtle: "#ede9fe"
  ink: "#1a1028"
  ink-secondary: "#374151"
  ink-muted: "#9ca3af"
  status-success: "#10b981"
  status-warn: "#f59e0b"
  status-danger: "#f43f5e"
  brand-barbours: "#7c3aed"
  brand-kokeshi: "#06b6d4"
  brand-apice: "#f59e0b"
  brand-lescent: "#ec4899"
  brand-rituaria: "#10b981"
typography:
  display:
    fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif"
    fontSize: "1.875rem"
    fontWeight: 700
    lineHeight: 1
    letterSpacing: "normal"
  headline:
    fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif"
    fontSize: "1.125rem"
    fontWeight: 700
    lineHeight: 1.3
    letterSpacing: "normal"
  title:
    fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif"
    fontSize: "0.875rem"
    fontWeight: 600
    lineHeight: 1.4
    letterSpacing: "normal"
  body:
    fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif"
    fontSize: "0.875rem"
    fontWeight: 400
    lineHeight: 1.5
    letterSpacing: "normal"
  label:
    fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif"
    fontSize: "0.75rem"
    fontWeight: 600
    lineHeight: 1
    letterSpacing: "0.08em"
rounded:
  full: "9999px"
  card: "16px"
  badge: "12px"
spacing:
  xs: "8px"
  sm: "16px"
  md: "20px"
  lg: "24px"
  xl: "32px"
components:
  button-primary:
    backgroundColor: "{colors.primary}"
    textColor: "{colors.surface-card}"
    rounded: "{rounded.badge}"
    padding: "8px 16px"
  button-primary-hover:
    backgroundColor: "{colors.primary-deep}"
    textColor: "{colors.surface-card}"
    rounded: "{rounded.badge}"
    padding: "8px 16px"
  chip-filter-active:
    backgroundColor: "{colors.primary}"
    textColor: "{colors.surface-card}"
    rounded: "{rounded.full}"
    padding: "6px 14px"
  chip-filter-idle:
    backgroundColor: "{colors.surface-card}"
    textColor: "{colors.ink-secondary}"
    rounded: "{rounded.full}"
    padding: "6px 14px"
  card-data:
    backgroundColor: "{colors.surface-card}"
    rounded: "{rounded.card}"
    padding: "{spacing.md}"
  brand-avatar:
    backgroundColor: "{colors.primary}"
    textColor: "{colors.surface-card}"
    rounded: "{rounded.badge}"
    size: "36px"
---

# Design System: Torre de Controle — GoBeauté

## 1. Overview

**Creative North Star: "The Brand Observatory"**

This is an elevated internal intelligence tool — the place where GoBeauté's commercial team watches its five brands navigate two marketplaces in real time. It has the calm authority of a brand that already knows it has something to say: data is presented with confidence, not apology. Numbers are large. Status reads at a distance. The lavender surface is a quiet container; the violet primary punctuates it with intention. The brands themselves — BARBOURS, KOKESHI, ÁPICE, LESCENT, RITUÁRIA — are protagonists, each with a signature color that anchors their identity throughout the tool.

The system rejects all four anti-references from the product brief by design. It is not the default BI dashboard (no charcoal sidebar, no generic electric-blue accent, no anonymous card grid). It is not a data dump (hierarchy is deliberate, summary first, depth on demand). It is not a gray form-builder. It is not a corporate spreadsheet. It earns the GoBeauté name on the inside of the product, not just the outside.

Components are tactile and confident: shadow presence at rest, stronger lift on hover, heavier border definitions. The interface responds to the user — it doesn't sit passively.

**Key Characteristics:**
- Lavender-tinted body field (#f8f7ff) as a quiet container
- Violet primary (#7c3aed) used with discipline — in brand marks, primary actions, and accent borders only
- Five named brand colors as a consistent visual language for brand identity
- System UI font stack — sharp, legible, zero cost. No decorative serifs.
- Rounded cards (16px) that sit clearly above the lavender surface
- Shadow at rest (ambient) + shadow lift on hover (structural)
- WCAG AA contrast throughout — labels are muted but never illegible

## 2. Colors

Violet owns its space. Everything else is quiet so the violet, the brand colors, and the status signals land with maximum clarity.

### Primary
- **Observatory Violet** (#7c3aed): The brand's internal voice. Used on primary action buttons, nav marks, icon badges, brand initials (BARBOURS), card borders in subtle form (#ede9fe), and focus rings. Never used decoratively or as body fill.
- **Observatory Violet Deep** (#6d28d9): Pressed and hover state of the primary. One step darker, used only in interactive state transitions — not as a default surface.
- **Observatory Violet Ink** (#2e1065): Near-black violet. Available for dark-mode readiness or high-contrast text on violet surfaces. Rarely needed at default contrast settings.

### Neutral
- **Lavender Field** (#f8f7ff): Page body background. A near-white with the faintest violet tint — barely perceptible but enough to set the system apart from a pure white canvas. Never used as a card background.
- **Observatory White** (#ffffff): All card surfaces. Sits clearly above the lavender field.
- **Hover Violet** (#f5f3ff): The resting hover surface for cards and table rows. One tonal step above the body bg.
- **Border Subtle** (#ede9fe): Card borders, table dividers, section separators. Violet-tinted, not neutral gray — invisible at a glance but establishes structure.
- **Ink** (#1a1028): Body text, large headings, data values. Deep purple-black. Used at full opacity only.
- **Ink Secondary** (#374151): Secondary text, table cell data, metadata.
- **Ink Muted** (#9ca3af): Placeholder labels, column headers, helper text. Minimum role: uppercase tracking label on white. Verify contrast before using at smaller sizes or lower weights.

### Secondary — Brand Identity Palette
Each brand owns one accent color used only for their avatar badge, chip, and data attribution. This palette must not migrate to other contexts (e.g., don't use KOKESHI cyan for a non-Kokeshi success state).
- **BARBOURS Violet** (#7c3aed): Same as Observatory Violet — BARBOURS is the brand closest to GoBeauté's primary identity.
- **KOKESHI Cyan** (#06b6d4): Electric cyan. Clear differentiation from the violet family.
- **ÁPICE Amber** (#f59e0b): Also serves as the global Status Warn color (shared deliberately — ÁPICE and caution share the same warm frequency).
- **LESCENT Pink** (#ec4899): Saturated warm pink. Distinct from red, distinct from violet.
- **RITUÁRIA Emerald** (#10b981): Also serves as the global Status Success color (shared deliberately — RITUÁRIA and on-target share the same green frequency).

### Tertiary — Status Signals
- **On Target** (#10b981): Attainment ≥ 100%. Green. Use only for status; no decorative use.
- **Near Target** (#f59e0b): Attainment 80–99%. Amber. Use only for status.
- **Off Target** (#f43f5e): Attainment < 80%. Rose-red. Use only for status.

### Named Rules
**The Violet Discipline Rule.** The primary violet (#7c3aed) is allowed on ≤4 distinct elements per screen: the nav mark, the active filter chip, brand avatars where applicable, and one primary CTA. If you're adding violet to a fifth element, you're decorating. Remove one of the four first.

**The Status Separation Rule.** Status colors (green / amber / red) have exactly one job each: expressing goal attainment, MoM direction, and live/demo badge state. They are forbidden as brand accents, section separators, or decorative fills.

## 3. Typography

**All weights and sizes:** System UI stack — `-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif`. No custom font is loaded. This is a deliberate choice: the system loads at native speed, renders identically across macOS, Windows, and Linux for an internal team, and the native stack already has excellent legibility at small sizes.

**Character:** Crisp, neutral, precision-instrument. The restraint of the font forces hierarchy through weight and size contrast, not personality. Display numerics are bold and large; labels are tight, tracked, and uppercase. The contrast is enough.

### Hierarchy
- **Display** (700 weight, 30px / 1.875rem, line-height 1): KPI primary values — GMV, order counts, average ticket. Numbers only. Never used for prose. Bold enough to read at arm's length.
- **Headline** (700 weight, 18px / 1.125rem, line-height 1.3): Page title in the top nav. Brand drill-down headings. Use sparingly — only one headline per screen.
- **Title** (600 weight, 14px / 0.875rem, line-height 1.4): Section headings within cards ("Performance por Brand", "Atingimento de Metas"). Card labels beside icons.
- **Body** (400 weight, 14px / 0.875rem, line-height 1.5): Table cell data, alert text, metadata. Max line length in prose contexts: 70ch.
- **Label** (600 weight, 12px / 0.75rem, line-height 1, letter-spacing 0.08em, UPPERCASE): Column headers, filter chips, badge text, section eyebrows. The eyebrow is earned in this system — each label identifies data infrastructure (a column, a filter, a status), not a decorative heading.

### Named Rules
**The Numeric Display Rule.** KPI numbers live at Display scale (700 weight, 30px minimum). Any number in a KPI card that drops below title size loses its authority. Tabular numerics only: `font-variant-numeric: tabular-nums` on any column of values so digits align under scroll.

**The Label Restraint Rule.** Uppercase tracking labels (LABEL role) are permitted in exactly two positions: table column headers and status badges. They must not appear as section eyebrows above card titles. Card titles use Title weight (600, 14px), not the uppercase label treatment.

## 4. Elevation

The system is **tonal-first, shadow-second**. Depth is established primarily by the three-layer background stack (lavender field → white card → hover tint), not by shadow intensity. Shadows confirm interactivity and separateness; they do not substitute for color contrast.

### Shadow Vocabulary
- **Ambient Rest** (`0 1px 2px 0 rgba(0, 0, 0, 0.05)`): All cards and containers at rest. The Tailwind `shadow-sm` value. Barely visible — just enough to lift white off lavender.
- **Structural Hover** (`0 4px 12px 0 rgba(124, 58, 237, 0.08), 0 1px 3px 0 rgba(0, 0, 0, 0.06)`): Applied on card or row hover. The violet tint in the outer shadow ties hover state to the brand. Do not use on non-interactive elements.

### Named Rules
**The Tonal Priority Rule.** Surface separation is achieved first by background color (lavender field → white card), then by border (#ede9fe), then by shadow. Do not reach for a heavier shadow when a tonal shift would do the same work.

**The Shadow-On-Hover Rule.** The Structural Hover shadow appears only on interactive elements (clickable cards, table rows, nav items). Static containers — alert boxes, footers, chart wrappers — stay at Ambient Rest or no shadow. A shadow on a static element implies interactivity it doesn't have.

## 5. Components

### Buttons
Buttons are rare in this tool (primarily a read-only dashboard), but appear in filter controls and period selectors.
- **Shape:** Gently rounded (12px badge radius). Not pill-shaped, not square.
- **Primary:** Observatory Violet (#7c3aed) background, white text, 600 weight, 12px uppercase label treatment. Padding: 8px 16px. Hover: deep violet (#6d28d9) + Structural Hover shadow lift.
- **Focus:** 2px ring at offset 2px, Observatory Violet color. Keyboard users must see this clearly.
- **Ghost / Filter idle:** White background, #ede9fe border, Ink Secondary text. Hover: Hover Violet (#f5f3ff) background.

### Filter Chips
The marketplace filter (All / TikTok / ML) and period selector use a chip pattern.
- **Active chip:** Observatory Violet (#7c3aed) fill, white text, rounded-full (9999px), 600 weight, 12px. No border.
- **Idle chip:** White fill, Border Subtle (#ede9fe) 1px border, Ink Secondary text. Hover: Hover Violet fill.
- **Rule:** Chips are the only UI element allowed to use rounded-full. All other interactive containers use badge (12px) or card (16px) radius.

### Cards / Containers
The primary surface unit. White on lavender, with violet-tinted borders.
- **Corner Style:** Gently rounded (16px — `rounded-2xl`).
- **Background:** Observatory White (#ffffff).
- **Shadow Strategy:** Ambient Rest at all times; Structural Hover when the card (or a row inside it) is interactive.
- **Border:** Border Subtle (#ede9fe), 1px — always present. The border is what defines the card edge; the shadow confirms it.
- **Internal Padding:** 20px (`p-5`) for content cards; 24px (`p-6`) for header-labeled section cards.
- **Rule:** No nested cards. Cards contain data, not other cards. Alert boxes inside a section use a tinted background (`amber-50`, `rose-50`, `emerald-50`), not a card shape.

### Brand Avatars
The signature component — each brand has a 36px square with colored background and white initials.
- **Shape:** 12px radius (`rounded-xl`) — matches the badge radius but distinctly not a circle, maintaining a grounded, structured feel.
- **Colors:** BARBOURS violet, KOKESHI cyan, ÁPICE amber, LESCENT pink, RITUÁRIA emerald.
- **Typography:** 12px, 700 weight, white — two-character initials (ÁP, BA, KO, LE, RI).
- **Rule:** The brand avatar is the only place brand colors appear as filled backgrounds. Brand colors do not fill chart bars, progress bars, or section headers.

### Data Table
The core data surface. Dense but readable.
- **Header row:** LABEL typography (600 weight, 12px, uppercase, tracked). Ink Muted (#9ca3af). No background — header cells sit on white card surface.
- **Body rows:** BODY typography (400 weight, 14px). Ink Secondary for data cells, Bold Ink for total/primary values.
- **Dividers:** Border Subtle (#ede9fe), 1px top border per row. No outer vertical borders.
- **Row hover:** Hover Violet (#f5f3ff) background + Structural Hover shadow on the row (not the whole card).
- **Alternating rows:** Subtle gray tint (`rgba(0,0,0,0.015)`) on odd rows. Not strongly visible — just enough to aid scanning in dense tables.
- **Numeric columns:** Right-aligned, `font-variant-numeric: tabular-nums`. Never center-aligned.

### KPI Cards
The headline metric unit. Two-column or four-column grid at the top of the dashboard.
- **Structure:** Label (top, uppercase, muted) → Icon badge (top right, colored accent square) → Display number (large, bold, Ink) → Subvalue (small, muted) → MoM delta (emerald or rose, bold).
- **Icon badges:** 32px × 32px, rounded-xl, colored per KPI semantic role (not per brand). Violet for GMV, cyan for orders, amber for ticket, emerald for spend.
- **Rule:** The KPI card hero number is the only Display-scale element. Labels above it stay at Label scale. There is no room for a second Display element in a KPI card.

### Goal Attainment Bars
The visual language for meta vs. real.
- **Bar track:** Gray-100 background, rounded-full, height 8px.
- **Bar fill:** On Target green (#10b981) for ≥ 100%, Near Target amber (#f59e0b) for 80–99%, Off Target rose (#f43f5e) for < 80%. Never the brand color.
- **Attainment badge:** Same three colors as tinted background + matching text (e.g. `emerald-50` / `emerald-700`). Shows percentage.
- **Rule:** The fill never exceeds 100% width visually — attainment overflow is communicated by the badge percentage (e.g. 191%) and badge color, not by a bar that breaks its track.

## 6. Do's and Don'ts

### Do:
- **Do** use the lavender field (#f8f7ff) as the only page background. It is the Observatory's atmosphere. Sections within the page are white cards on lavender — never lavender sections within a white page.
- **Do** make KPI numbers large and bold (30px, 700 weight). The manager who glances at the dashboard reads these numbers first; they must answer immediately.
- **Do** use brand colors exclusively in brand avatar badges. KOKESHI cyan belongs to KOKESHI — nowhere else.
- **Do** apply `font-variant-numeric: tabular-nums` on every column of numbers. Shifting digit widths break the precision-instrument feel instantly.
- **Do** use shadow-lift (Structural Hover) on every interactive card and row. The tool's tactile character comes from the consistent, branded hover response.
- **Do** show status signals (goal attainment, MoM delta) in the system's three semantic colors: emerald success, amber warn, rose danger. Never use brand colors for status.
- **Do** cap the uppercase label treatment to table column headers and status badges. Section card titles use Title weight (600, 14px, mixed case) — not the UPPERCASE LABEL style.

### Don't:
- **Don't** use a charcoal or dark sidebar as primary navigation. This is the Metabase default aesthetic — the exact reference the product brief names as forbidden. Navigation lives in the page header; it is minimal and violet-accented.
- **Don't** lay out data as a "data dump": oversized chart blocks with no hierarchy, crowded side-by-side tables without whitespace. This is the Looker anti-pattern named in the brief. Summary first, depth on demand.
- **Don't** use Retool's dense-gray form-builder look: gray backgrounds on interactive elements, no rounded corners, no brand color presence. Every interactive element in this system has a violet or brand-color identity.
- **Don't** use beige, sand, or warm-tinted backgrounds. The body is a cool lavender (#f8f7ff), not a warm neutral. Warm backgrounds are the corporate Excel anti-pattern.
- **Don't** place side-stripe borders (`border-left` > 1px as colored accent) on alert boxes or cards. Alerts use a fully tinted background (amber-50, rose-50, emerald-50) with a matching full border, not a decorative left stripe.
- **Don't** use gradient text (`background-clip: text` with a gradient). Data values are single-color ink. Emphasis comes from weight and size.
- **Don't** use glassmorphism (blurred translucent overlays) as a default surface treatment. This system's surfaces are opaque: lavender field, white cards. Blurs are forbidden except in a deliberate, rare overlay context.
- **Don't** let brand colors appear in chart bar fills, section fills, or progress bar fills. Brand colors are reserved for identity (avatar badges only). Chart bars use a unified violet scale; status bars use semantic green/amber/red.
- **Don't** let the attainment progress bar visually exceed 100% width — use the percentage badge for overflow, not bar overflow.
- **Don't** put display-scale (30px) numbers anywhere except KPI metric values. Large numbers imply a headline KPI; using that scale for secondary data (goal targets, date labels) inflates visual noise.
