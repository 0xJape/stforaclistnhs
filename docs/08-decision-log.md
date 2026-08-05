# Decision Log

Add one row when decision is made. Do not bury decisions in chat or code comments.

| ID | Decision needed | Options | Owner | Due | Status | Decision / rationale |
|---|---|---|---|---|---|---|
| D-001 | Product mode | Academic scenario / operational early warning | Project team | 2026-07-27 | Decided | Operational pilot supporting capstone review; claims remain scenario-level until validation. |
| D-002 | Frontend map library | Leaflet / MapLibre | Project team | 2026-07-27 | Decided | MapLibre. |
| D-003 | Backend direction | Python / Node gateway | Project team | 2026-07-27 | Decided | Keep Python primary; no Node gateway. |
| D-004 | Real surveillance data source | TBD | Provincial Health Office | Before live ingest | Open | Owner identified; exact system/feed pending. |
| D-005 | Data approver | TBD | Provincial Health Office | Before live ingest | Open | Exact approving role pending. |
| D-006 | Alert policy | Dashboard / reviewed send / automatic | Project team | 2026-07-27 | Decided | Dashboard-only pilot; no external notifications. |
| D-007 | Recipient groups | TBD | Provincial Health Office | Before Make.com | Deferred | Decide only after alert validation. |
| D-008 | Deployment target | Local / network / cloud | Project team | 2026-07-27 | Decided | Local host exposed through Cloudflare Tunnel. |
| D-009 | Public date horizon | Fixed / administrator configured | Project team | 2026-07-27 | Decided | Administrator configures approved public date range. |
| D-010 | Output terminology | Scenario / forecast / early warning | Project team | 2026-07-27 | Decided with gate | Current outputs: “dengue risk scenario.” “Early warning signal” only after real-data validation and approval. |
| D-011 | Public access | Private / public / split | Project team | 2026-07-27 | Decided | Public approved map snapshots, analytics, reports; protected management functions. |
| D-012 | Authentication | OIDC / email-password | Project team | 2026-07-27 | Decided | Administrator-created email/password accounts; no public registration. |
| D-013 | Roles | Viewer / Analyst / Data Manager / Administrator | Project team | 2026-07-27 | Decided | All four roles required. |
| D-014 | Publication owner | Administrator / two-step approval | Project team | 2026-07-27 | Decided | Administrator publishes approved run and date range. |
| D-015 | Corrections | Overwrite / versioned audit | Project team | 2026-07-27 | Decided | Versioned correction with actor, reason, and timestamp. |
| D-016 | Run cadence | Monthly / scheduled / on demand | Project team | 2026-07-27 | Decided | On-demand runs. |
| D-017 | Weather | Display / store / model input | Project team | 2026-07-27 | Decided | Store versioned weather data for future validation only. |
| D-018 | Export formats | CSV / GeoJSON / HTML / PNG / PDF | Project team | 2026-07-27 | Decided | CSV, GeoJSON, printable HTML, PNG. |
| D-019 | Region XII expansion | Immediate / gated | Project team | 2026-07-27 | Decided | Gate on pilot acceptance, official region-wide data, and validated municipality model. |
