# Frontend MVP

## Build order

Build only Spatial Intelligence first. Existing Python API already supplies geometry, snapshots, rankings, alerts, clusters, timelines, and summaries.

## Spatial Intelligence requirements

### Initial load

1. Fetch municipality/barangay geometry once.
2. Fetch `/api/dates`.
3. Default to first available or latest selected scenario date.
4. Fetch `/api/snapshot?date=...`, `/api/ranking?date=...`, `/api/alerts?date=...`, `/api/clusters?date=...`.

### Map

- full-screen map
- barangay fill based on `CASE_COLOR_INTENSITY` or selected documented metric
- municipality and province boundaries
- hover tooltip: barangay, municipality, projected cases, probability, alert level
- click selection: details drawer/panel
- visible legend and scale definition
- keyboard reachable controls and non-color-only alert labels

### Timeline

- month slider with labeled date
- play/pause
- cancel stale requests when date changes quickly
- no geometry refetch during playback

### Details panel

Show:

- projected posterior mean
- lower/upper credible cases
- outbreak probability and threshold
- alert level and reason
- dominant outbreak factor
- whether observed update was applied
- barangay timeline link/chart

### Empty/error behavior

- loading state without map jump
- clear API unavailable message
- no-data state for missing date/PSGC
- retain last valid map state if next request fails

## Confirmed technology and style

- React frontend
- MapLibre map
- dark GIS operations-center visual direction
- neutral ORACLIS styling until final branding exists
- public and authenticated application states

MapLibre must key feature state and colors by PSGC. Do not hardcode feature order.

## Public surface

- administrator-approved map snapshots
- administrator-approved analytics
- administrator-approved printable reports
- administrator-configured public date range
- CSV, GeoJSON, printable HTML, and PNG snapshot exports only when explicitly published

## Authenticated surface

- viewer: approved non-public views
- analyst: detailed aggregate outputs and exports
- data manager: aggregate observation submission/correction
- administrator: accounts, roles, runs, public date range, and publication

## Out of MVP

- authentication UI
- direct data upload
- alert configuration
- dashboards with many generic cards
- region-wide boundary layers
