# Barangay Case Reporting — Minimal Implementation Plan

## Goal

Let one authorized account per barangay record dengue cases, show current case situation, and update ORACLIS forecasts from validated case aggregates.

## Critical boundaries

- Patient records are private operational data. Never expose them through public map, exports, logs, AI prompts, or forecast SQLite files.
- Patient registry in this project is **demo-only synthetic data** until written governance approval exists. It must display `DEMO DATA — NOT REAL PATIENTS` and remain separate from approved aggregate reports.
- Demo registry records never affect map observed totals, forecasts, Make.com alerts, exports, or model validation.
- Prediction receives aggregate case counts only: `reporting period + PSGC + observed cases`.
- Account PSGC comes from server-side assignment. Client cannot choose or override another barangay.
- New records do not directly mutate a published forecast. They validate, commit, aggregate, queue one model run, then require a successful run before replacement.
- Existing engine is monthly. Its current 16-day endpoint is a weather-context scenario, not a validated daily dengue forecast. UI must label this distinction until a daily case model is trained and validated.

## Roles

| Role | Scope |
|---|---|
| Barangay Encoder | Create and view records for assigned barangay; correct own pending submissions |
| Health Data Reviewer | Review duplicates, corrections, and questionable records; approve model input |
| Administrator | Create/disable accounts, assign PSGC, manage runs and publication |
| Public Viewer | See published aggregate situation and forecast only |

## Minimal data model

Use one operational SQLite database initially. Keep generated forecast SQLite files read-only and separate.

### `users`

- `id`
- `email_normalized` — unique
- `password_hash` — Argon2id
- `role`
- `assigned_psgc` — required for Barangay Encoder
- `is_active`
- `created_at`, `last_login_at`

### `patient_cases`

- `id` — random UUID
- `assigned_psgc` — copied from authenticated account, never request body
- `patient_reference` — local health-office reference, encrypted or keyed hash; do not use as public ID
- `patient_name_encrypted`
- `address_encrypted`
- `birth_date_encrypted` or `age_band`
- `sex`
- `date_of_onset`
- `date_reported`
- `case_classification` — suspected/probable/confirmed/discarded
- `case_status` — active/recovered/deceased
- `review_status` — pending/approved/rejected
- `created_by`, `created_at`, `updated_at`
- `version`, `supersedes_case_id`, `correction_reason`

Identifiable fields exist because requested, but minimum collection remains preferred. Exact fields need Provincial Health Office approval before implementation.

For current demo scope, use synthetic display name, age band, sex, barangay PSGC, onset/report dates, classification, and case status only. Omit address, contact data, government IDs, real reference numbers, photos, and free-text notes. Add a reset operation that clears demo registry records before any production use.

### `case_aggregates`

- `period_start`
- `psgc`
- `approved_case_count`
- `generated_at`
- unique key: `period_start + psgc`

This table is model boundary. Monthly aggregates map directly to current engine input contract.

### `model_runs`

- `id`, `status`, `requested_at`, `started_at`, `finished_at`
- `input_version`, `output_path`, `error_summary`
- statuses: queued/running/succeeded/failed/superseded

### `audit_log`

- `id`, `actor_id`, `action`, `entity_type`, `entity_id`, `timestamp`
- `before_json`, `after_json`, `request_id`
- never store passwords or plaintext patient identifiers

## Submission and forecast flow

1. Barangay Encoder logs in.
2. Backend derives assigned PSGC from session.
3. Backend validates required fields, dates, classification, and likely duplicate.
4. Case saves as `pending`; audit event records creation.
5. Health Data Reviewer approves case.
6. Transaction rebuilds affected monthly `case_aggregates` row.
7. Run queue coalesces rapid updates into one pending run.
8. Worker exports approved aggregates to current `DATE,PSGC,OBSERVED_CASES,EXPOSURE` contract and runs actual spatiotemporal/Bayesian model.
9. Successful output becomes latest internal run. Public publication remains separate administrator action.
10. Situation dashboard refreshes immediately from approved aggregates; forecast refreshes only after successful run.

## Make.com case-aware alerts

- Eligible high-risk barangay notifications join successful forecast output to approved `case_aggregates` by `PSGC` and reporting period.
- Payload includes approved observed total, projected cases and interval, outbreak probability, weather context, data freshness, and forecast freshness.
- Message labels `Approved observed cases`, `Model projection`, and `16-day weather context` separately.
- Dispatcher never reads or sends patient rows or identifiers.
- Pending, rejected, discarded, and superseded cases never affect alert totals.
- Public messages apply approved small-count suppression; authorized internal routes follow recipient policy.
- Missing/stale approved aggregate is reported as unavailable, never converted to zero.
- Deduplication includes alert onset and data version so approved corrections can produce a controlled update.
- Full payload, Make filters, bilingual copy, and acceptance checks live in [05-alerting-and-automation.md](05-alerting-and-automation.md).

## Current situation dashboard

Authenticated barangay view:

- approved cases this month
- pending and rejected submission counts
- active/recovered/deceased counts
- suspected/probable/confirmed counts
- change from previous comparable period
- recent case trend
- last approved-data timestamp
- last successful model-run timestamp

Public/province map:

- aggregate counts and rates only
- current month versus previous month
- barangay risk level and forecast uncertainty
- no small-cell patient breakdowns; define suppression threshold with data owner
- clear labels: `Observed`, `Model forecast`, and `16-day weather context`

## Minimal API

### Authentication

- `POST /api/auth/login`
- `POST /api/auth/logout`
- `GET /api/auth/me`

### Barangay case entry

- `GET /api/cases?status=...` — assigned PSGC only
- `POST /api/cases`
- `POST /api/cases/{id}/corrections`
- `GET /api/situation/me`

### Reviewer/admin

- `GET /api/review/cases`
- `POST /api/review/cases/{id}/approve`
- `POST /api/review/cases/{id}/reject`
- `GET /api/model-runs`
- `POST /api/model-runs/{id}/publish`

Public endpoints continue reading published aggregate artifacts only.

## CRUD behavior and integrity

### Accounts

- **Create:** Administrator creates account and assigns exactly one valid barangay PSGC.
- **Read:** Administrator lists accounts; Encoder reads own profile only.
- **Update:** Administrator changes role, barangay assignment, or active state. Reassignment requires confirmation and audit entry.
- **Delete:** Disable account and revoke all sessions. Do not hard-delete account referenced by audit or case records.

### Patient cases

- **Create:** Encoder submits case for session-assigned PSGC. Server ignores/rejects client-supplied PSGC, validates fields, checks likely duplicates, and saves `pending`.
- **Read:** Encoder sees assigned barangay only. Reviewer sees permitted review queue. Lists return minimum fields; identifying details require a separate authorized request.
- **Update:** Pending record may be edited with optimistic concurrency using `version`. Approved record is immutable; correction creates a new version linked through `supersedes_case_id`.
- **Delete:** No hard delete. Encoder may withdraw a pending record with reason. Reviewer may mark a record rejected or discarded. Retention-policy deletion anonymizes identifiers while preserving aggregate and audit integrity.

### Review and aggregates

- Approval/rejection is an explicit state transition, not a general update.
- Approval and aggregate rebuild happen in one database transaction.
- Repeated approval requests are idempotent.
- Aggregate rows are generated from approved, non-superseded records; clients cannot edit them directly.
- Database constraints enforce valid enums, required timestamps, foreign keys, unique email, aggregate uniqueness, and non-negative counts.

### Model runs and publication

- Model runs are append-only operational records.
- Only one worker may hold the run lock.
- New requests coalesce while a run is queued or running.
- Publication accepts succeeded runs only and changes pointer/state atomically.
- Failed runs never replace latest valid internal or published output.

### API response rules

- Use consistent JSON envelope: `data`, `error`, `requestId`.
- Use stable machine-readable errors such as `VALIDATION_ERROR`, `FORBIDDEN`, `CONFLICT`, and `RUN_FAILED`.
- Return field-level validation messages without returning secrets or unrelated patient data.
- Use pagination and server-side filters for case and audit lists.
- Require an idempotency key for create, correction, approval, and rerun requests.
- Return `409 Conflict` for stale versions and duplicate transitions.

## Frontend integration and UI states

Keep existing map-first, dark GIS operations-center look. Case reporting is an authenticated workspace/drawer reached from existing navigation; it must not replace or visually compete with Spatial Intelligence map.

### Layout

- Preserve full-screen MapLibre dashboard, timeline, panels, typography, neutral charcoal surfaces, red risk accents, and existing spacing tokens.
- Add `Report case` primary action for Barangay Encoder.
- Add compact `Current situation` section to assigned-barangay details panel.
- Add restricted case table in a drawer or dedicated authenticated workspace; no patient names on map, tooltips, rankings, or public cards.
- Reuse existing modal surface and button styles instead of adding another design system.
- Keep responsive behavior: full-screen modal/drawer on narrow screens, map state preserved behind it.

### Loading

- Show skeletons matching final card/table dimensions to prevent layout jump.
- Disable submit button and show one progress label while mutation is pending.
- Use row-level progress for approve/reject; do not block unrelated rows.
- Keep last valid map and situation data visible during refresh with a subtle `Updating` indicator.
- Show model states separately: `Queued`, `Running`, `Succeeded`, `Failed`; never imply case save means forecast completed.
- Cancel stale reads when filters or selected barangay change.

### Errors and recovery

- Inline field errors appear beside inputs; focus first invalid field after submit.
- Page/API failure shows clear message, request ID, and retry action.
- Mutation failure preserves entered form data and re-enables controls.
- `401` opens login flow after preserving safe navigation intent; never preserve patient form content in URL or browser storage.
- `403` shows access-denied state without leaking whether another barangay record exists.
- `409` stale-version conflict shows latest record and offers reload; never overwrite silently.
- Network loss shows offline banner. Do not queue identifiable patient submissions in service-worker, localStorage, or IndexedDB.
- Unexpected errors send sanitized diagnostics only; no patient fields in telemetry or console logs.

### Modals and confirmations

- Case create/edit uses modal or drawer with sections: patient, dengue episode, classification, review summary.
- Review modal shows minimum necessary case data, duplicate warnings, and approve/reject actions.
- Reject, withdraw, correct approved record, account reassignment, account disable, rerun, and publication require explicit confirmation.
- Destructive/state-changing confirmation names affected record/action and requires reason where audit policy needs one.
- Success uses non-blocking status/toast and refreshes affected aggregate; errors stay visible until dismissed or corrected.
- Modal traps focus, has visible title, uses `aria-modal="true"`, closes by explicit button/Escape when safe, and restores focus to trigger.
- Dirty form asks before close. During final submission, prevent duplicate close/submit without trapping user indefinitely.

### Validation

- Client validation improves feedback; server remains authority.
- Validate required fields, reasonable date ordering, allowed enum values, reference length, and age/date consistency.
- Reject future onset/report dates beyond approved reporting rule.
- Duplicate warning compares keyed patient reference and approved matching policy; reviewer decides non-exact matches.
- Never reveal matching patient identity in duplicate warnings to unauthorized users.

### Accessibility and privacy

- Every input has persistent label and associated error text.
- Status never depends on color alone; include icon/text.
- Table, drawer, modal, and map controls remain keyboard usable.
- Loading announcements use polite live region; validation/error summary uses assertive announcement.
- Respect `prefers-reduced-motion`.
- Mask patient references in lists and after inactivity; require reauthentication for high-risk reveal/export if later approved.
- No identifiable-data export in MVP.

## Verification and acceptance checks

- Encoder cannot create, read, update, withdraw, or infer records outside assigned PSGC.
- Disabled account sessions stop working immediately.
- Duplicate submit/idempotent retry creates one case only.
- Two simultaneous edits produce one success and one `409`, with no lost update.
- Approved case correction preserves old version and adjusts aggregate exactly once.
- Rejected/discarded/superseded cases do not count in model input.
- Save failure retains form values; retry succeeds without duplicate.
- Failed model run leaves previous forecast visible and labels situation data as newer than forecast.
- Modal keyboard/focus behavior and narrow-screen layout pass manual check.
- Logs, API errors, forecast SQLite, public endpoints, browser storage, and telemetry contain no patient identifiers.
- Backup restore reproduces account, case version, aggregate, audit, and publication state.

## Delivery slices

### Slice 0 — governance gate

- name data controller and reviewer
- approve exact patient fields, legal basis/consent, retention, deletion, breach response, backups, and small-count suppression
- decide encryption-key owner and recovery process

**Done when:** written approval exists. Do not collect identifiable data before this gate.

### Slice 1 — secure aggregate MVP

- authentication and one-PSGC account assignment
- aggregate case-count submission only
- validation, review, audit, current-situation cards
- manual model rerun using approved monthly aggregates

**Done when:** cross-barangay access tests fail closed and approved counts update situation view.

### Slice 2 — patient registry

- encrypted identifying fields
- correction/version workflow and duplicate detection
- restricted patient list; no patient export by default
- backup/restore and retention job

**Done when:** access, audit, encryption, restore, and data-leak tests pass.

### Slice 3 — queued forecast refresh

- coalescing single-worker run queue
- input snapshot/version tied to every run
- failed run keeps previous forecast
- successful internal forecast refresh notification

**Done when:** concurrent submissions produce one deterministic run and no partial publication.

### Slice 4 — forecast validation

- backtest actual-case model on official observed history
- compare against weather-only and persistence baselines
- publish accuracy and uncertainty by horizon
- only call 16-day result a dengue forecast if daily target data and validation support it

**Done when:** documented model beats agreed baseline without leakage.

## Database decision

SQLite holds Slice 1 for a local single-host pilot with serialized writes and one model worker. Move operational database to PostgreSQL before broad multi-barangay rollout if concurrent writes, remote workers, high availability, or managed backups become requirements. No PostGIS needed unless operational spatial queries exceed existing PSGC/GeoJSON lookup.

## First build target

Build Slice 1 first. It proves account isolation, reporting workflow, current-situation analytics, and real-case model input without exposing identifiable patient data. Add patient registry only after governance gate passes.
