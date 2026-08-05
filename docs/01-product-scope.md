# Product Scope

## Current product truth

ORACLIS currently generates dengue **scenario projections** for 199 barangays across 11 South Cotabato municipalities/cities. It is not yet validated operational outbreak-detection software.

## Confirmed direction

- Mode: operational pilot supporting capstone review
- Users: panel viewers, health analysts, municipal health officers, data managers, administrators
- Geography: South Cotabato first; Region XII only after province pilot acceptance, official region-wide data, and validated municipality model
- Hosting: local service exposed through Cloudflare Tunnel
- Public surface: administrator-approved map snapshots, analytics, and reports only
- Product wording: use **dengue risk scenario** until validation passes; **early warning signal** is reserved for approved real-data runs

## MVP outcome

A disease-surveillance intelligence interface where users can:

- select month and inspect barangay risk on map
- inspect credible interval, risk level, alert reason, and contributing factors
- view rankings, clusters, and municipality/barangay timeline
- export generated data and read model limitations

## First release pages

| Page | Purpose | Must include |
|---|---|---|
| Spatial Intelligence | Main working screen | map, month selector, playback, selection panel, rankings, alerts/clusters |
| AI Intelligence | Explain generated output | timeline, uncertainty, factor shares, model/scenario notes |
| Data & Reports | Trace output and export | dataset/run metadata, filters, export, limitations |

## Non-goals now

- hospital/patient case management
- diagnosis or clinical decision-making
- public outbreak declarations
- Region XII risk map before risk data covers all Region XII areas
- retraining ML models in browser or frontend
- user/admin CRUD beyond minimum data upload workflow

## Remaining scope decisions

- Minimum supported browser/device
- English only, or Filipino/local-language labels
- Exact criteria and approver for moving from “risk scenario” to “early warning signal”
- Terms, disclaimer, and public-data publication policy

## Acceptance criteria

- UI calls current read-only API without manual CSV processing.
- Every forecast view shows scenario limitation language.
- Map colors key by PSGC, never municipality display order.
- No alert is presented as confirmed outbreak.
