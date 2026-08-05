# Region XII (Philippines) Boundary Integration Guide

This folder contains GeoJSON administrative boundaries for Region XII (SOCCSKSARGEN),
Philippines, extracted from the ORACLIS project. Use this guide to wire the same
boundaries into another project.

## Folder contents

```
geojson/
  region12-municities.json                       Region-wide municipality boundaries (main layer)
  provdists-region-1200000000.0.001.json          Region-wide province boundaries (overlay layer)
  municities-provdist-1204700000.0.001.json       Municipalities — North Cotabato
  municities-provdist-1206300000.0.001.json       Municipalities — South Cotabato
  municities-provdist-1206500000.0.001.json       Municipalities — Sultan Kudarat
  municities-provdist-1208000000.0.001.json       Municipalities — Sarangani
  region12.json                                   Region XII outline (single polygon/boundary)
```

Province PSGC codes used above:
- `1204700000` — Cotabato (North Cotabato)
- `1206300000` — South Cotabato
- `1206500000` — Sultan Kudarat
- `1208000000` — Sarangani

(General Santos City is an independent city within these municipality files, not a province.)

Source data follows the **faeldon/philippines-json-maps** PSGC schema. If you need other
regions/provinces or want fresher data, pull directly from that dataset.

## GeoJSON schema

All files are standard `FeatureCollection`s. Each feature's `properties` object includes:

```ts
{
  id: string;            // normalized id used by the app (may equal adm3_psgc)
  name?: string;          // normalized display name
  province?: string;      // normalized province name
  adm3_psgc?: number;      // PSGC code — municipality/city level
  adm3_en?: string;        // official municipality/city name
  adm2_psgc?: number;      // PSGC code — province level
  adm2_en?: string;        // official province name
  adm1_psgc?: number;      // PSGC code — region level
  geo_level?: string;      // 'Mun'/'City'/'Prov' etc.
}
geometry: {
  type: 'Polygon' | 'MultiPolygon';
  coordinates: number[][][] | number[][][][];
}
```

Not every file guarantees every field — some only have `adm3_*`/`adm2_*` (raw dataset),
others have been normalized to add `id`/`name`/`province`. Always fall back:

```ts
const name = props.name ?? props.adm3_en ?? 'Unknown';
const province = props.province ?? props.adm2_en ?? '';
const id = String(props.id ?? props.adm3_psgc);
```

## Integration steps

### 1. Copy files
Drop the `geojson/` folder into your project's static assets directory
(e.g. `public/geojson/`, `static/geojson/`, or wherever your framework serves
static files from). No build step needed — they're just JSON.

### 2. Fetch at runtime
```ts
const res = await fetch('/geojson/region12-municities.json');
const geoData: GeoJSON.FeatureCollection = await res.json();
```

### 3a. If using MapLibre GL / Mapbox GL
```ts
map.addSource('municipalities', {
  type: 'geojson',
  data: geoData,
  promoteId: 'id', // lets you use setFeatureState for hover/selection by this id
});

map.addLayer({
  id: 'municipalities-fill',
  type: 'fill',
  source: 'municipalities',
  paint: {
    'fill-color': '#1E293B',
    'fill-opacity': ['case', ['boolean', ['feature-state', 'hover'], false], 0.9, 0.7],
  },
});

map.addLayer({
  id: 'municipalities-stroke',
  type: 'line',
  source: 'municipalities',
  paint: { 'line-color': 'rgba(255,255,255,0.12)', 'line-width': 0.6 },
});
```

To auto-fit the map to the loaded boundaries:
```ts
const bounds = new maplibregl.LngLatBounds();
geoData.features.forEach(f => {
  if (!f.geometry) return;
  const coords = f.geometry.type === 'Polygon'
    ? f.geometry.coordinates.flat()
    : f.geometry.coordinates.flat(2);
  coords.forEach(c => { if (c[0] && c[1]) bounds.extend([c[0], c[1]]); });
});
if (!bounds.isEmpty()) map.fitBounds(bounds, { padding: 40, duration: 800 });
```

Layer province boundaries the same way but add them **before** the municipality
source/layers so municipalities render on top. A low fill-opacity (~0.08) with a
solid stroke works well for a province-tinted background.

### 3b. If using Leaflet
```ts
L.geoJSON(geoData, {
  style: { color: '#94A3B8', weight: 0.6, fillOpacity: 0.15 },
  onEachFeature: (feature, layer) => {
    layer.on('click', () => {
      const id = String(feature.properties.id ?? feature.properties.adm3_psgc);
      // handle selection
    });
  },
}).addTo(map);
```

### 3c. If not using a map library at all
The GeoJSON is still useful for point-in-polygon lookups, area calculations, or
static rendering — libraries like `@turf/turf` work directly against these files
without any map renderer.

## Region XII map center/zoom reference

If you need a sensible starting viewport:
```ts
const REGION_XII_CENTER: [number, number] = [124.685, 6.40]; // [lng, lat]
const REGION_XII_ZOOM = 7.8;
```

## Notes for whoever integrates this (agent instructions)

1. Confirm the target project's map library first (MapLibre/Mapbox GL, Leaflet,
   or none) before writing layer code — the GeoJSON itself doesn't change, only
   the rendering layer does.
2. Check whether the target project already has a `public/` or `static/` folder
   convention and place `geojson/` there, don't invent a new folder structure.
3. If the target project needs per-municipality risk/data coloring, key colors
   off `properties.id` (or `adm3_psgc` if `id` is absent) via a `match` expression
   (MapLibre) or a style callback (Leaflet) — do not hardcode municipality order.
4. `region12-municities.json` and `provdists-region-1200000000.0.001.json` are
   ~2.2MB and ~2.1MB respectively (full polygon detail). If load time or bundle
   size matters, consider simplifying geometry with `mapshaper` or `@turf/simplify`
   before shipping — this was not done in the source project.
5. Don't rename the files unless the target project's conventions require it —
   keeping names as-is makes it easy to diff against the original ORACLIS source
   if the boundaries ever need updating.
