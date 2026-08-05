# Delivery Roadmap

## Phase 1 — prove current baseline

- run `VERIFY_PACKAGE.bat`
- run `RUN_SYSTEM.bat`
- inspect latest report, map, SQLite, match report
- record baseline results and known issues
- measure full-run duration, output size, and API response times
- test package verification, real-boundary run, API endpoints, and manual output review

**Done when:** package verification passes and output artifacts are understood.

## Phase 2 — frontend Spatial Intelligence MVP

- create React + MapLibre frontend with dark GIS operations-center style
- load current API geometry and selected-date data
- add timeline, map styles, ranking, details panel, clusters
- add visible scenario warning and accessible legend

**Done when:** user can inspect any returned month/barangay without reading CSV files.

## Phase 3 — analytics and reports

- add timeline/uncertainty charts
- expose run metadata and limitations
- add export of read-only filtered data

**Done when:** project demo explains what, where, when, and why without claiming confirmed outbreaks.

## Phase 4 — real-data governance

- finalize source/data owner
- normalize and validate live observations
- add administrator-created accounts and Viewer/Analyst/Data Manager/Administrator role checks
- add versioned corrections and audit history
- add administrator-controlled publication and public date range
- preserve provenance/audit records

**Done when:** approved aggregate observations can update scenarios safely.

## Phase 5 — validation and alerts

- validate using real observed history
- define reviewed alert policy
- add deduplicated signed webhook
- integrate Make.com sandbox, then approved channels

**Done when:** alert messages are governed, auditable, and scientifically defensible.

## Phase 6 — production platform

- deploy with auth, strict CORS, secrets management, monitoring, backups
- schedule ingestion and model runs
- decide database upgrade only if needed

**Done when:** service has named owner and operating procedure.

## Immediate next task

Phase 1 baseline verification. Do not scaffold frontend until baseline evidence and API contract are recorded.
