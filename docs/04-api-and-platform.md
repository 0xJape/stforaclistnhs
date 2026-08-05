# API and Platform

## Existing platform

`src/spatiotemporal_runtime_api.py` exposes latest successful SQLite simulation run through Python stdlib HTTP server.

It is development-only:

- binds localhost by default
- read-only GET API
- permissive CORS default (`*`)
- no authentication or authorization
- reads generated SQLite output

## Confirmed deployment direction

- keep Python as primary backend
- run service on local host
- expose public site through Cloudflare Tunnel
- use application login for protected functions
- administrator creates accounts; no public registration
- use email/password login

Public tunnel must not expose current development API directly. Reverse proxy/application routing must expose an explicit public read-only contract and protect authenticated routes.

## Authentication requirements

Email/password auth requires:

- modern password hashing (`Argon2id` preferred)
- unique normalized email
- secure `HttpOnly`, `Secure`, `SameSite` session cookies
- CSRF protection for state-changing requests
- login rate limiting and generic failure messages
- session revocation, inactivity expiry, and logout
- administrator-controlled account disable/reset process
- role checks enforced by backend, never frontend alone
- audit records for login, account, data, run, and publication actions

## Frontend API contract

| Need | Endpoint |
|---|---|
| Health/version | `/api/health`, `/api/version` |
| Available months | `/api/dates` |
| Map snapshot | `/api/snapshot?date=YYYY-MM-01` |
| Ranking | `/api/ranking?date=...&metric=...&limit=...` |
| Alerts | `/api/alerts?date=...` |
| Clusters | `/api/clusters?date=...` |
| One barangay timeline | `/api/timeline?psgc=...` |
| Boundaries | `/api/geometry/barangays`, `/api/geometry/municipalities`, `/api/geometry/province` |

## Required before deployment

- set specific CORS origin
- add authentication and role checks before exposing protected functions
- add request logging, health monitoring, and error-safe responses
- version API response fields
- define generated-run retention and storage limit
- decide static frontend hosting: same server vs separate service

## Storage decision

Keep SQLite for local demo/single read-only instance. Consider PostgreSQL/PostGIS only when concurrent users, managed uploads, audit history, or spatial querying exceeds SQLite workflow.

## Secrets

- no API keys in repo, `.env`, output folders, or browser bundle
- use environment variables locally
- use managed secret store in cloud

## Publication model

- data/run generation and public publication are separate actions
- administrator selects approved run and visible date range
- public endpoints return published artifacts only
- admin/data routes never rely on obscurity or hidden URLs

## API work direction

No Node backend. Extend Python minimally for authentication, role checks, versioned observation records, run control, publication state, exports, and audit history after baseline verification.
