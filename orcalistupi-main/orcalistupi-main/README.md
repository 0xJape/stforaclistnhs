# ORACLIS South Cotabato Backend

This package contains the working South Cotabato dengue scenario engine, its local read-only API, live-observation ingestion utility, source datasets, and weighted-ensemble calibration artifact. Historical monthly barangay values were interpolated from annual data, so long-range outputs are scenario projections and not official outbreak declarations.

## Scope

- Province: South Cotabato
- Map level: 199 barangays across 11 cities and municipalities
- Forecast period: January 2026 to December 2050
- Alert logic: individual alerts, probabilistic spatio-temporal alerts, and connected clusters of at least three red barangays
- Main output: full-screen Leaflet map, charts, tables, GeoJSON, CSV, and SQLite

## Windows requirements

Install standard 64-bit Python 3.14.x. The launcher creates its environment at `%LOCALAPPDATA%\ORACLIS\py314` to avoid Windows path-length installation errors.

## Run the system

1. Extract the complete folder to a short path such as `C:\ORACLIS`.
2. Keep internet access enabled on the first run so real barangay boundaries can be downloaded and validated.
3. Double-click `VERIFY_PACKAGE.bat`. It installs the runtime dependencies and completes an offline mock simulation test.
4. Double-click `RUN_SYSTEM.bat`.
5. The newest report and Leaflet map open automatically after a successful run.

Generated runs are saved under `outputs/spatiotemporal_run_YYYYMMDD_HHMMSS`.

## Start the API

Run the simulation at least once, then double-click `START_API.bat`.

Default address: `http://127.0.0.1:8765`

Common endpoints:

- `GET /api/health`
- `GET /api/version`
- `GET /api/dates`
- `GET /api/barangays`
- `GET /api/municipalities`
- `GET /api/snapshot?date=2026-01-01`
- `GET /api/ranking?date=2026-01-01`
- `GET /api/timeline?psgc=1206302002`
- `GET /api/alerts?date=2026-01-01`
- `GET /api/clusters?date=2026-01-01`
- `GET /api/geometry/barangays`
- `GET /api/geometry/municipalities`
- `GET /api/geometry/province`
- `GET /map`

The API reads the SQLite database in the latest successful run. The next frontend should load the geometry once, then request snapshots, rankings, alerts, clusters, and timelines as the selected month changes.

## Connect live observations

External provider records must be converted to these columns:

```csv
DATE,PSGC,OBSERVED_CASES,EXPOSURE
2026-08-01,1206302002,8,1
```

Validate and merge a CSV or JSON file with:

```powershell
python integration\ingest_live_observations.py --input path\to\observations.csv --mode upsert
```

The default destination is `data/observed_cases_updates.csv`. Run `RUN_SYSTEM.bat` again after ingestion so the Bayesian update and generated outputs use the new observations.

Supported modes:

- `upsert`: replace matching date and PSGC records and keep all others
- `append`: reject duplicate date and PSGC records
- `replace`: replace the complete update file
- `--dry-run`: validate without writing

The developer connecting a live provider should keep authentication and provider-specific field mapping outside the forecasting engine, then pass normalized records to the ingestion utility.

## Cache Open-Meteo weather

Weather data is stored separately from observed dengue cases and does not alter forecasts until a backtest validates a weather model. Fetch municipality-level daily weather and monthly aggregates with:

```powershell
python integration\ingest_open_meteo_weather.py --start-date 2026-01-01 --end-date 2026-07-27
```

It uses generated municipality boundaries to select one point per municipality, calls Open-Meteo without an API key, and upserts:

- `data/weather/open_meteo_daily.csv`
- `data/weather/open_meteo_monthly.csv`

Use `--dry-run` to validate provider response without writing cache files.

## Configuration

`spatiotemporal_config.json` controls the forecast dates, spatial diffusion, Bayesian uncertainty, alert thresholds, red-cluster rule, playback speed, boundary behavior, and output settings.

`.env.example` lists the optional API environment variables. Copy it to `.env` only when the selected runtime or frontend tooling loads environment files.

## Important folders

- `src`: simulation, boundary validation, and API code
- `integration`: live-data validation and ingestion
- `integration/send_make_alerts.py`: disabled-by-default signed Make.com webhook dispatcher for deduplicated HIGH/CRITICAL risk onsets
- `data`: source data and observation updates
- `models`: weighted-ensemble artifact and calibration table
- `outputs`: generated at runtime

## GitHub transfer

A GitHub repository is the easiest handoff method because the next developer can clone it, create branches, review changes, and preserve version history. Upload this cleaned folder as the repository root. Do not commit `.env`, API keys, generated `outputs`, downloaded `data/cache`, virtual environments, or local logs. The included `.gitignore` excludes those files.
