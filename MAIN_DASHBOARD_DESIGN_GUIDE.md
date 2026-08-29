# Main Dashboard Design Guide

## Purpose

Reuse this design pattern for future spatial-intelligence projects.

**Goal:** Make dashboard feel like spatial intelligence center, not CRUD system.

- Full-screen dark interface
- Map as main visual
- Minimal navigation
- Red, orange, yellow, green risk colors
- Cyan weather and data accents
- White map boundaries
- Dark glass-like panels
- Large readable numbers
- Small uppercase labels
- Collapsible side panels
- Detail appears after map selection

## 1. Main Layout

```text
┌──────────────────────────────────────────────────────────────┐
│ Logo        Location/status       Methodology Analytics Login │
├───────────────┬──────────────────────────────┬───────────────┤
│ Intelligence  │                              │ Selected area  │
│ controls      │          Full map             │ Risk details   │
│ legend        │                              │ Ranking        │
│ KPIs          │                              │ Charts         │
│ charts        │                              │                │
└───────────────┴──────────────────────────────┴───────────────┘
```

Use three primary regions:

1. Top navigation
2. Left intelligence panel
3. Center map
4. Right selected-area panel

Map must remain dominant. Sidebars support map, not dominate it.

## 2. Main Map Design

The map should answer:

> Where is the problem?

### Map layers

1. Dark or muted basemap
2. Province boundary
3. Barangay boundaries
4. Municipality labels
5. Barangay labels at high zoom
6. Risk polygon fills
7. Heatmap surface
8. Alert markers
9. Selected barangay outline
10. Selected municipality outline

### Color system

```text
Low risk       green
Moderate risk  yellow
High risk      orange
Critical risk  red
No data        dark gray
Weather        cyan → yellow → orange
```

Use thin white boundaries. Use thicker white province outline. Use red outline for selected location.

## 3. Map Interaction

Required interactions:

- Hover barangay → show tooltip
- Click barangay → open detail panel
- Select metric → recolor map
- Toggle map layers
- Zoom to labels
- Select date
- Previous/next date
- Play timeline automatically
- Collapse left panel
- Collapse right panel
- Highlight selected municipality
- Display alert rings for critical areas

Keep map controls simple. Avoid putting every action in buttons.

## 4. Left Panel Design

Left panel explains map meaning.

Include:

- Page title
- Short description
- Metric selector
- Gradient legend
- KPI cards
- Model/data notice
- Small charts

Example:

```text
MAP INTELLIGENCE

Dengue risk
scenario

Bayesian spatio-temporal projection
across mapped areas.

Visualization layer
[Outbreak probability ▼]

0% risk ━━━━━━━━━ 100% risk

Projected cases       1,240
Critical areas          12
Largest cluster         18

Scenario output
Not official outbreak declaration.
```

Panel should collapse so map can become dominant.

## 5. Right Panel Design

Right panel explains selected location.

Before selection:

```text
SELECTED BARANGAY

Select map area
Click any map area to inspect risk.
```

After selection:

```text
SELECTED BARANGAY

Barangay Name
Municipality Name

HIGH

72%
outbreak probability

Projected cases       18.4
95% credible range    12–26
Hotspot score          2.41
Spatial pressure       8.2

Forecast and uncertainty
[line chart]

Priority ranking
1. Barangay A          82%
2. Barangay B          76%
3. Barangay C          71%
```

Use progressive disclosure. Show summary first. Put deeper analysis in modal or analytics page.

## 6. Timeline Design

Place timeline near bottom of map.

```text
‹  January 2025 ━━━━━━━●━━━━━━━ December 2026  ›
                         ▶ Play
```

Timeline requirements:

- Slider
- Current month label
- Previous button
- Next button
- Play/pause button
- Loading state
- Data status
- Historical and predicted distinction

Animation speed should feel controlled. Around 1–2 seconds per step works well.

## 7. Layer Control Design

Use one compact `Layers` button. Open modal or popover.

```text
MAP CONTROLS

☑ Spatiotemporal surface
  Smoothed scenario intensity

☑ Barangay risk fill
  Selected metric by barangay

☑ Barangay boundaries
  White geographic outlines

☑ Barangay labels
  Visible when zoomed in

☑ Municipality labels
  Geographic context

☑ Outbreak alerts
  Critical-area markers
```

Do not expose raw MapLibre concepts to users.

## 8. Detail Modal Design

Clicking barangay opens focused analysis.

Show:

- Barangay name
- Municipality
- Alert level
- Probability
- Projected cases
- Credible interval
- Hotspot score
- Spatial lag
- Forecast line chart
- Risk driver
- Historical timeline

Use dark modal. Red accent for risk. Avoid overcrowding.

## 9. Weather Mode Design

Use same map shell. Change data context, not whole design.

Header:

```text
South Cotabato · 16-day weather intelligence
```

Left panel:

```text
16-day weather
intelligence

Municipality temperature,
rainfall, and atmospheric risk outlook.

Municipalities       11
Forecast days        16
Weather records      176
```

Weather cards should show:

- Date
- Temperature range
- Rain probability
- Rainfall millimeters
- Municipality count

Weather supports dengue context. It should not look like a separate weather app.

## 10. Design System

### Backgrounds

```css
--bg: #081116;
--panel: #101c22;
--panel-dark: #0c171d;
--border: #2b3b42;
```

### Text

```css
--text: #eef5f6;
--muted: #a7b8bd;
--dim: #82969d;
```

### Accents

```css
--red: #e5484d;
--red-light: #ffb0b3;
--yellow: #ffd166;
--orange: #ff7a3d;
--cyan: #00b8d9;
--green: #00c878;
```

### Typography

- Use clean sans-serif
- Strong headings
- Small uppercase eyebrow labels
- High contrast
- Large KPI numbers
- Avoid long paragraphs

## 11. Responsive Behavior

### Desktop

- Three-column layout
- Map remains central
- Panels collapse

### Tablet

- Left panel collapses by default
- Right panel becomes overlay

### Mobile

- Map fills screen
- Bottom sheet replaces right panel
- Layer controls become floating buttons
- Timeline becomes bottom control
- Labels reduce or disappear

Never allow panels to permanently hide map on small screens.

## 12. Dependencies Used in ORACLIS Design

Required frontend dependencies:

```json
{
  "react": "^19.2.7",
  "react-dom": "^19.2.7",
  "maplibre-gl": "^6.0.0",
  "recharts": "^3.10.1"
}
```

Development dependencies:

```json
{
  "typescript": "~6.0.2",
  "vite": "^8.1.1",
  "@vitejs/plugin-react": "^6.0.3",
  "@types/react": "^19.2.17",
  "@types/react-dom": "^19.2.3",
  "@types/node": "^24.13.2",
  "oxlint": "^1.71.0"
}
```

For design-only reuse, use:

- React
- React DOM
- MapLibre GL
- Recharts
- Vite
- TypeScript

Backend and Python dependencies are not needed unless next project needs live data or model output.

## 13. Implementation Order

Tell agent:

1. Create dark dashboard shell.
2. Build full-screen three-column layout.
3. Add MapLibre map container.
4. Add GeoJSON boundary layers.
5. Add risk color layers.
6. Add labels and selected-area outlines.
7. Add left intelligence panel.
8. Add right detail panel.
9. Add map tooltip and click selection.
10. Add timeline controls.
11. Add layer toggle modal.
12. Add responsive behavior.
13. Add charts and detail modal.
14. Polish spacing, colors, animations, and accessibility.

Start with static mock data. Connect APIs later.

## Design Rule

Map stays main character. Panels explain map. Every control answers one spatial question.
