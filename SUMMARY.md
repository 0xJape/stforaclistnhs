# ORACLIS v3 — Developer Turnover Summary

_Last updated: July 28, 2026_

## 1. System purpose

ORACLIS is a South Cotabato dengue spatial-intelligence prototype. It combines barangay-level spatiotemporal projections, GeoJSON mapping, weather context, analytics, an evidence-based assistant, and Make.com automation.

Important interpretation limits:

- Forecasts are scenario projections, not official outbreak declarations.
- Historical monthly barangay values include interpolation from annual data.
- Weather is context only unless a validated backtest establishes causal predictive value.
- Public alerts must remain subject to LGU, health-office, and governance approval.

## 2. Repository layout

```text
ORACLISv3/
├─ frontend/                                  React/Vite dashboard
│  ├─ src/App.tsx                            Main dashboard, MapLibre map, weather and alert UI
│  ├─ src/App.css                            Dashboard styling
│  ├─ public/maps/                           Browser-ready South Cotabato GeoJSON
│  ├─ package.json
│  └─ vite.config.ts                         Local API proxy
├─ orcalistupi-main/orcalistupi-main/        Python engine and runtime API
│  ├─ src/spatiotemporal_runtime_api.py      HTTP API
│  ├─ src/weather_facebook.py                Facebook card/caption and Make webhook sender
│  ├─ src/run_spatiotemporal_bayesian.py     Simulation and output generation
│  ├─ src/oraclis_agent.py                   Evidence-grounded assistant
│  ├─ integration/                           Observation/weather ingestion and alert utilities
│  ├─ data/                                  Source and update datasets
│  ├─ models/                                Weighted ensemble artifacts
│  ├─ outputs/                               Generated run directories and SQLite databases
│  ├─ START_API.bat                          API launcher
│  ├─ RUN_SYSTEM.bat                         Simulation launcher
│  ├─ requirements.txt
│  └─ .env.example
├─ docs/                                     Alerting and Make.com workflow notes
├─ region12-boundaries-export/               Region XII boundary source files
└─ PROJECT_OVERVIEW.md                       Original product direction
```

Despite old planning text saying “Backend: Node.js,” current backend is Python.

## 3. Technology stack

### Frontend

- React 19
- TypeScript 6
- Vite 8
- MapLibre GL 6
- Recharts 3

### Backend

- Python 3.14
- Python standard-library HTTP server
- SQLite
- NumPy, pandas, SciPy, Matplotlib, Requests, Shapely
- Open-Meteo weather data
- Make.com webhook → Facebook Page photo post

## 4. Local startup

### Backend

Preferred interpreter:

```text
C:\Website_Projects\ORACLISv3\.venv-1\Scripts\python.exe
```

From `orcalistupi-main/orcalistupi-main`, run `START_API.bat`.

Default API:

```text
http://127.0.0.1:8765
```

`START_API.bat` first uses workspace `.venv-1`, then falls back to:

```text
%LOCALAPPDATA%\ORACLIS\py314\Scripts\python.exe
```

This preference is deliberate. Earlier launchers used a stale packaged environment, so code edits appeared ineffective after restart.

API requires a successful generated run. It selects a run containing:

```text
outputs/spatiotemporal_run_*/database/oraclis_spatiotemporal.sqlite
```

### Frontend

From `frontend`:

```text
npm install
npm run dev -- --host 127.0.0.1 --port 5174
```

Dashboard:

```text
http://127.0.0.1:5174
```

`vite.config.ts` proxies `/api` and `/files` to `http://127.0.0.1:8765` during local development.

### Validation

Frontend production build:

```text
npm run build
```

Last validated result: build passed. Current non-blocking warnings:

- CSS `@import` is not first statement.
- Main JavaScript bundle exceeds Vite's 500 kB warning threshold.

## 5. Main API behavior

Common endpoints include:

- `GET /api/health`
- `GET /api/version`
- `GET /api/dates`
- `GET /api/snapshot?date=YYYY-MM-DD`
- `GET /api/municipality-summary?date=YYYY-MM-DD`
- `GET /api/clusters?date=YYYY-MM-DD`
- `GET /api/timeline?psgc=...`
- Weather forecast/live/scenario endpoints
- Agent ask/transcription/speech endpoints
- `POST /api/weather/facebook-post`

`POST /api/weather/facebook-post` accepts JSON containing:

- `municipality`: required municipality name
- `map_image`: required `data:image/jpeg;base64,...` current map capture

Request body limit is 5 MB. Map JPEG must decode successfully, be non-empty, and not exceed 3 MB.

## 6. Work completed in this development session

### Dashboard and chart improvements

- Refined dashboard presentation and weather alert workflow.
- Modernized rainfall visualization.
- Added send-confirmation modal, progress text, disabled/loading state, success state, and retry path.
- Alert action now creates one combined Facebook photo instead of separate/legacy email or SMS behavior.

### Combined Facebook card

`src/weather_facebook.py` generates an exact 1200×1200 PNG containing:

1. Current South Cotabato risk-map capture.
2. Sixteen-day rainfall bars.
3. Rain-probability line.
4. Municipality and wet-streak labels.
5. ORACLIS/LGU/PAGASA context.

Important implementation details:

- Browser map arrives as JPEG.
- Matplotlib explicitly decodes it using `format="jpeg"`.
- Output uses a 12×12-inch figure at 100 DPI, producing 1200×1200 pixels.
- Minimum map-byte rejection was removed because small but valid browser JPEGs were previously rejected as `Map capture size is invalid.`

### Map capture and black-image fix

MapLibre is initialized with:

```text
preserveDrawingBuffer: true
```

`captureAlertMap()` in `frontend/src/App.tsx`:

1. Requests a fresh MapLibre render.
2. Waits for the `render` event.
3. Copies the WebGL canvas to an offscreen 2D canvas.
4. Samples pixels and rejects a near-black/empty image.
5. Exports JPEG at quality `0.9`.

Critical fix: do **not** call `map.resize()` after opening the send modal. That resized the source canvas from roughly `861×775` to `356×62`, causing malformed or black captures. The resize call was removed.

Also removed duplicate map initialization, duplicate navigation controls, and unreachable duplicate cleanup code introduced during an earlier patch.

### Dengue-awareness caption

Every new Facebook alert now includes English and Filipino dengue-specific wording:

- Heading explicitly says dengue awareness/risk alert.
- Explains that standing rainwater can become breeding habitat for dengue-carrying mosquitoes.
- Advises keeping surroundings clean and emptying or covering water containers.
- Advises mosquito protection, especially during daytime.
- Lists warning signs: high fever, severe headache, pain behind eyes, muscle/joint pain, rash, or unusual bleeding.
- Advises prompt medical consultation when symptoms occur.
- Directs readers to LGU and PAGASA advisories.
- States that alert is automated and is not a diagnosis, official emergency advisory, or confirmed outbreak declaration.

Existing Facebook posts do not update. Caption changes apply only to newly created posts.

## 7. Make.com and Facebook integration

Backend sends signed multipart form data containing:

- `metadata`: JSON, including `facebook_message`
- `photo`: generated `oraclis-weather-warning.png`
- `X-ORACLIS-Signature`: HMAC-SHA256 signature
- `X-ORACLIS-Event-ID`: deterministic event identifier

Required environment variables:

```text
MAKE_WEBHOOK_URL=https://hook.make.com/...
MAKE_WEBHOOK_SECRET=<long random shared secret>
MAKE_ALERTS_ENABLED=true
```

Keep `MAKE_ALERTS_ENABLED=false` until sandbox testing, Page approval, and governance review are complete.

Security notes:

- Never commit real webhook URL, shared secret, Facebook token, or `.env`.
- Verify HMAC in Make.com before accepting payload.
- Use event ID for deduplication.
- Keep credentials only in backend hosting environment.
- Do not trigger public Facebook posts during UI or image debugging; intercept/mock endpoint instead.

Important environment-loading note: current Python code reads `os.getenv(...)` but does not load `.env` itself. `.env.example` is documentation. Set variables in process/hosting environment, modify launcher to load them securely, or add an approved environment loader before deployment.

Detailed Make instructions exist in:

- `docs/05-alerting-and-automation.md`
- `docs/06-make-facebook-photo-workflow.md`

## 8. Safe test procedure

Avoid live public publishing while testing.

1. Keep `MAKE_ALERTS_ENABLED=false`, or intercept `/api/weather/facebook-post` in browser automation.
2. Open dashboard and enter intelligence map.
3. Wait until status says `199 barangay boundaries loaded`.
4. Open weather workflow for a municipality meeting at least three continuous wet days.
5. Confirm captured image is a normal-sized JPEG and contains visible map pixels.
6. Generate card locally or mock webhook response.
7. Confirm final PNG is 1200×1200.
8. Inspect English and Filipino caption metadata.
9. Only enable Make after stakeholder approval.

The latest browser-side mocked request produced a JPEG payload without publishing. Frontend build passed after capture fix.

## 9. Known risks and technical debt

- `frontend/src/App.tsx` is very large and includes `// @ts-nocheck`; TypeScript cannot currently provide full safety there.
- Some source lines are heavily minified/condensed, increasing merge and maintenance risk.
- CSS import-order warning should be cleaned up.
- Frontend bundle is large; split analytics/assistant routes only if load performance becomes a measured issue.
- Python API uses a basic long-running standard-library server; assess production concurrency, TLS, observability, authentication, and rate limiting before public exposure.
- API currently allows configurable CORS, defaulting to `*`. Production must restrict it to deployed frontend domain.
- Public Facebook endpoint needs authentication/authorization beyond CORS before internet exposure. CORS is not access control.
- SQLite and generated outputs live on local disk. Treat them as read-only packaged artifacts or move mutable state to persistent storage.
- OpenStreetMap raster tiles require internet access and attribution. Cross-origin/WebGL rendering can affect browser capture; keep pixel validation.
- Weather provider availability can block alert validation.
- Forecast event ID is deterministic by municipality/date range; Make.com should handle duplicate delivery safely.
- No automated end-to-end test currently verifies final photo composition without webhook publication.

## 10. Deployment guidance

### Recommended split deployment

Use:

- **Vercel:** React/Vite frontend.
- **Render, Railway, Azure App Service, container host, or VPS:** Python API.

Why backend should not be moved unchanged to Vercel:

- It runs a persistent server on port 8765.
- It reads local SQLite/generated-run files.
- It performs Matplotlib image composition.
- It expects filesystem data and can perform longer model/weather operations.
- Serverless execution limits and ephemeral storage are a poor fit.

Production frontend work required:

1. Replace local-only proxy assumptions with configurable backend base URL, such as `VITE_API_URL`.
2. Route all API requests through that base URL or configure Vercel rewrites.
3. Set backend `ORACLIS_CORS_ORIGIN` to exact Vercel domain.
4. Keep GeoJSON in `frontend/public/maps` or approved CDN/object storage.
5. Add Vercel SPA rewrite so client routes return `index.html`.

Production backend work required:

1. Package known-good generated SQLite database and required weather/model assets, or move data to managed persistent storage.
2. Bind to host/port supplied by hosting platform.
3. Add TLS through hosting proxy.
4. Add endpoint authentication, rate limiting, structured logs, health checks, and error monitoring.
5. Configure Make variables through hosting secrets.
6. Restrict CORS.
7. Test image generation memory and request duration under host limits.
8. Confirm OpenStreetMap/Open-Meteo access from hosting region.

Suggested architecture:

```text
Browser
  → Vercel React frontend
  → hosted Python API
      → SQLite/read-only model artifacts or persistent database
      → Open-Meteo
      → signed Make.com webhook
          → Facebook Page
```

## 11. Data and model handoff

Before transfer:

- Keep source datasets and model artifacts versioned where licensing permits.
- Do not commit virtual environments, secrets, logs, caches, or unnecessary generated runs.
- Preserve at least one verified run database needed by API.
- Document which run is approved for demonstration/production.
- Record dataset origin, update date, interpolation method, model version, and validation metrics.
- Validate all PSGC identifiers and exactly 199 South Cotabato barangay geometries.
- Use `integration/ingest_live_observations.py` for normalized updates.
- Re-run simulation after observation ingestion.

## 12. Immediate next-developer checklist

1. Clone/copy repository and create clean Python/Node environments.
2. Run package verification and confirm one valid SQLite run exists.
3. Start API and verify `/api/health`.
4. Start frontend and wait for 199 boundaries.
5. Run `npm run build`.
6. Test weather alert with webhook disabled or intercepted.
7. Confirm map capture is visible and final card is 1200×1200.
8. Review both dengue captions with health-domain stakeholder.
9. Review Make.com signature/deduplication and Facebook Page permissions.
10. Add production authentication before exposing alert endpoint.
11. Choose split deployment host and configure frontend API URL/CORS.
12. Keep public posting disabled until final governance approval.

## 13. Final status

Working locally:

- Main spatial-intelligence dashboard
- 199-barangay MapLibre visualization
- Forecast timeline and analytics
- Weather context views
- Combined map/rainfall Facebook card
- Black-map capture prevention
- Bilingual dengue-awareness caption
- Signed Make.com multipart workflow
- Frontend production build

Not production-ready without:

- Backend hosting configuration
- Endpoint authentication and rate limiting
- Restricted CORS
- Managed secrets
- Persistent/approved data strategy
- Monitoring and operational review
- Public-health content and publication governance approval
