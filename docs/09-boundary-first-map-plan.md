# Boundary-First Map Plan

## Goal

Render complete South Cotabato barangay map before API responds. Forecast API only adds scenario colors, ranking, and selected-barangay statistics.

## Current fault

`App.tsx` fetches `/api/geometry/barangays` inside MapLibre `load`. If backend or Vite proxy is unavailable, no barangay source or layers exist. User sees only basemap.

## Target flow

1. Browser loads versioned static boundary GeoJSON from frontend bundle.
2. Map adds barangay source, fill, outline, hover, click, and selected layers immediately.
3. Browser requests `/api/dates`, then `/api/snapshot?date=...` separately.
4. Matching forecast rows set feature state using PSGC.
5. API failure leaves boundary map usable and shows `Scenario data unavailable`; no map layer removed.

## Files

| File | Change |
|---|---|
| `frontend/public/maps/south_cotabato_barangays_2023.geojson` | Copy trusted cache geometry snapshot; served with frontend, no backend required. |
| `frontend/src/App.tsx` | Load static geometry first; API loading stays independent; retain explicit string PSGC feature IDs. |
| `frontend/src/App.css` | Optional neutral boundary-only legend/status styling only if needed. |
| `src/spatiotemporal_runtime_api.py` | Keep `/api/geometry/barangays` as API fallback/export endpoint. No frontend dependency on it. |

## Data contracts

### Boundary geometry

- Source: `data/cache/south_cotabato_barangays_2023.geojson`.
- Scope: 199 South Cotabato barangays.
- Stable identity: `ORACLIS_PSGC`, normalized to string `psgc` and assigned to GeoJSON `feature.id`.
- Display fields: `ORACLIS_BARANGAY`, `ORACLIS_LOCALITY`.

### Forecast data

- Endpoint: `/api/snapshot?date=YYYY-MM-DD`.
- Join key: `String(row.PSGC) === feature.id`.
- No geometry comes from forecast response.
- Rows without matching geometry: ignored and counted in console warning during development.
- Geometry without forecast: neutral fill, still clickable/visible.

## Failure behavior

| Failure | Required UI result |
|---|---|
| API unavailable / 502 | Map boundaries remain visible; neutral fill; scenario status reports unavailable. |
| Snapshot abort during month change | Ignore abort; keep current visible state until newer request succeeds. |
| Static GeoJSON missing/corrupt | Show explicit `Map geometry unavailable` state; do not falsely claim API failure. |
| Forecast PSGC mismatch | Keep polygon neutral; no crash. |
| OSM tile outage | Boundaries and data layers still render over blank background. |
| API restart | Future date requests recover; geometry never reloads from API. |

## Implementation sequence

1. Copy cache GeoJSON to `frontend/public/maps/` without transforming coordinates.
2. Change geometry request from `/api/geometry/barangays` to `/maps/south_cotabato_barangays_2023.geojson`.
3. Validate GeoJSON before adding source: `FeatureCollection`, 199 features, every feature has PSGC, polygon or multipolygon geometry.
4. Add map source/layers after static geometry succeeds.
5. Keep dates/snapshot effects independent from map initialization.
6. Add one status distinction: `Map ready; waiting for scenario data` vs `Scenario data unavailable`.
7. Keep backend geometry endpoint for integrations and manual verification; do not remove it.

## Acceptance checks

1. Stop API. Reload frontend. All 199 boundaries appear.
2. Start API. Reload frontend. Fills, ranking, and selected barangay appear.
3. Stop API after load. Change map pan/zoom. Boundaries remain.
4. Advance timeline while API runs. Colors and numbers change without map recreation.
5. Verify first geometry PSGC equals an API snapshot PSGC.
6. Run `npm run build` and `npm run lint`.

## Deferred

- Offline forecast snapshots.
- Service worker/PWA cache.
- Geometry simplification/vector tiles.
- Production CDN cache headers.

Add these only when static GeoJSON load time or deployment scale requires them.
