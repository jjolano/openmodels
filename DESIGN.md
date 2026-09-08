---
name: OpenModels
description: Technical instrument panel for model composition and SDK handoff.
colors:
  bg: "#10161d"
  panel: "#17212b"
  raised: "#1c2a36"
  ink: "#e4edf5"
  muted: "#9cabb9"
  line: "#354452"
  accent: "#78dce5"
  code: "#0c1218"
  warning: "#efc17d"
  light-bg: "#f2f5f7"
  light-panel: "#fff"
  light-raised: "#e8eef3"
  light-ink: "#182a38"
  light-muted: "#516574"
  light-line: "#c1ced8"
  light-accent: "#006778"
  light-code: "#e8eef3"
  light-warning: "#87530a"
typography:
  body:
    fontFamily: 'ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif'
    fontSize: "15px"
    lineHeight: 1.55
  headline:
    fontSize: "28px"
    lineHeight: 1.25
    letterSpacing: "-.025em"
  code:
    fontFamily: "ui-monospace, SFMono-Regular, Consolas, monospace"
    fontSize: "12px"
    lineHeight: 1.7
rounded:
  control: "4px"
  code: "3px"
spacing:
  panel-gap: "16px"
  content-inset: "20px"
components:
  button-primary:
    backgroundColor: "{colors.accent}"
    textColor: "{colors.bg}"
    rounded: "{rounded.control}"
    padding: "10px 14px"
---

# Design System: OpenModels

## Overview

**Creative North Star: "Technical instrument panel"**

Graphite surfaces and crisp divisions organize a compact developer workspace. Readable
native controls carry actions; monospace values and code carry exact technical information.
This captures the implemented system in [web/style.css](web/style.css) and the approved
brand commitments in [PRODUCT.md](PRODUCT.md).

## Colors

Cyan identifies selection, links and the primary export action. Amber draws attention to
settings errors and missing information. Neutral surfaces separate the page, panels,
selected rows and code without decorative imagery. Light mode replaces all nine color
properties together; use the CSS properties so components follow the selected theme.

## Typography

System sans-serif is the body and control face. Monospace is reserved for code, revisions
and technical values, with tabular numerals. Panel headings use 17px; metadata uses 12px,
with 11px detail labels. Mobile workbench headings use 25px. Avoid enlarging interface
headings into promotional heroes.

## Layout

The page is centered at a maximum width of 1600px with 32px horizontal padding. The
workbench has three columns and 16px gaps; at 1000px or below it becomes two columns with
the inspector spanning both. At 650px or below it stacks pipeline, library and inspector,
with 14px page padding. Above 1300px the pipeline receives more width.

The component list scrolls within 340px (320px on mobile); the inspector is capped at
430px and export code at 320px. Library pagination limits each result page to 30 entries.

## Elevation & Depth

Panels use one-pixel borders and tonal surfaces. Selection uses an accent border or a
one-pixel inset accent outline. There are no floating shadows or gradients.

## Shapes

Panels and component rows have square edges. Native controls and the primary action use
the control radius; code blocks use the smaller code radius. Dividers organize dense data.

## Components

- Native buttons, inputs, selects and textareas share panel backgrounds and line borders.
  Standard controls have a 40px minimum height; compact paging controls use 36px.
- Navigation uses an accent underline for the current page. Code-view buttons use the same
  underline language and expose selection through `aria-pressed`.
- Component rows and fixed pipeline slots retain text labels alongside their selected state.
- Keyboard focus uses a 2px accent outline with 4px offset. The skip link becomes visible on
  focus. Placeholders use the muted text color.
- Color transitions last .12s; slot selection lasts .18s. Both run only when the user has
  not requested reduced motion.

## Do's and Don'ts

- Do use shared CSS properties for both themes.
- Do keep source settings, missing values and exported code readable at compact sizes.
- Do retain native controls, visible keyboard focus and the stacked mobile layout.
- Don't imply that a browser composition request is a validated or qualified recipe.
- Don't replace crisp divisions with decorative shadows or oversized cards.
