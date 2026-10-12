# ORACLIS Update Plan

**Project:** ORACLIS — outbreak risk analysis and monitoring system

**Goal:** Improve understanding of outbreak predictions, local risk information, alerts, guidance, and safe data entry through a clear, accessible interface.

## Scope

### 1. Dashboard and map

- Keep a visible map legend explaining colors, risk levels, markers, and symbols.
- Add barangay search that centers and zooms the map and shows available risk information.
- Add pinned locations for barangays and health facilities. Support add, remove, and revisit.
- Export current map view as an image with visible layers, legend where practical, title, and export date.
- Make map controls and risk levels understandable without color alone through labels, symbols, and contrast.

### 2. Patient and symptom data

Authorized users may record only required fields:

- Name — restricted patient data
- Age
- Gender
- Municipality
- Barangay
- Purok, if applicable
- Contact number — restricted patient data
- Dengue symptoms
- Record date

Requirements:

- Use role-based access for patient-level records.
- Never expose identifying patient data in public maps or general dashboards.
- Validate required fields, dates, locations, age, contact number, and symptoms.
- Show clear validation errors.
- Confirm retention, consent, encryption, audit logging, and data-controller requirements before production use.

### 3. Predictions and transparency

- Show prediction variables and defined model contribution percentages.
- Explain that contributions are model signals, not proof of cause or certainty.
- Show location, forecast period, risk level, model update time, and data freshness.

### 4. Alerts and notifications

- Add dedicated current-alert interface with severity, location, issue date, source, and next steps.
- Add searchable alert history with location, severity, and date filters.
- Link to verified official health-government pages and preserve source plus timestamp.
- Add notification center with read state and related alert/forecast/system links.
- Add notification preferences if supported by current roles and backend.

### 5. Forecast history

- Save and display previous forecasts.
- Compare forecasts across dates and locations.
- Distinguish historical predictions from current predictions.
- Show forecast dates, model version/update, and data period.

### 6. AI assistant

- Improve answers about ORACLIS, dengue risk, maps, forecasts, and alerts.
- Ground answers in approved ORACLIS data and trusted health sources.
- Identify sources for health guidance.
- Do not diagnose or present predictions as certainty.
- Direct medical concerns to qualified professionals or official services.
- Add suggested questions, loading/error states, and new-conversation action.

### 7. Dengue knowledge hub

- Add searchable topics for dengue, mosquitoes, symptoms, prevention, and care-seeking.
- Prefer reviewed official or reputable sources.
- Show source and publication/last-reviewed date.
- State that education does not replace medical advice.

### 8. Help and support

- Add user guide for dashboard, map, forecasts, alerts, and main features.
- Add report-problem form with category, description, and optional evidence.
- Add administrator contact method/form.
- Show submission confirmation and next steps.

### 9. Onboarding

- Add first-time walkthrough for legend, search, alerts, and forecast history.
- Support next, previous, skip, and reopening from Help/Settings.
- Keep steps short and focused.

### 10. Interface and accessibility

- Add active, loading, success, and error feedback.
- Add dark/light theme switch persisted between visits.
- Support desktop, tablet, and mobile layouts.
- Use consistent navigation for dashboard, alerts, notifications, forecasts, knowledge, help, and settings.

## Implementation order

1. Inspect current routes, components, APIs, database, authentication, roles, map library, and prediction pipeline.
2. Add dashboard/map basics: legend, barangay search, pins, export, and accessibility labels.
3. Add alert interface, history, notifications, and verified source links.
4. Add forecast history and model-variable explanations.
5. Improve AI assistant and add knowledge hub.
6. Add patient symptom records only after access-control and privacy design is approved.
7. Add support, onboarding, themes, and responsive refinements.
8. Test desktop/mobile, loading, empty, failure, unavailable-service, and restricted-permission states.

## Architecture constraints

- Reuse existing React/Vite frontend, MapLibre map, Python HTTP API, SQLite runtime, and current auth/reporting model.
- Reuse current data contracts where possible.
- Do not invent live data, official health links, model contributions, or patient records.
- If backend behavior or data source is missing, add a clearly marked integration point and document dependency.
- Keep public views free of patient identifiers.
- Treat forecasts as scenario projections, not official outbreak declarations.

## Acceptance criteria

- Every feature has a clear entry point and understandable labels.
- Search, filters, map navigation, and exports work with available data.
- Alerts and forecasts show location, time period, and source.
- Patient records are role-restricted and never shown in public views.
- Contribution percentages have a clear definition and are not causal claims.
- Missing data and unavailable services produce usable states.
- Theme, responsive layout, keyboard access, labels, contrast, and feedback work across supported devices.

## Proposed implementation phases

### Phase 0 — discovery and contracts

Inventory current frontend routes/components, backend endpoints, SQLite tables, auth roles, map state, prediction fields, and test coverage. Produce a feature-to-existing-contract matrix before code changes.

### Phase 1 — map foundation

Implement legend, barangay search, map focus, pins, image export, non-color risk labels, and responsive map controls. Add focused UI checks.

### Phase 2 — alerts and forecasts

Implement alert views/history/filtering, source metadata, notification center, forecast history, comparison, freshness, and contribution explanation using only available backend fields.

### Phase 3 — assistant and knowledge

Improve grounded assistant UX and add source-backed knowledge content with review metadata and medical safety language.

**Implemented:** searchable WHO-backed dengue topics, visible source/review metadata, urgent-care guidance, medical-advice limits, assistant health-question grounding, suggested questions, loading/error feedback, and new-conversation control.

### Phase 4 — restricted patient records

Design and implement patient symptom entry only after confirming roles, authorization, retention, audit, privacy, and storage requirements. Add server-side validation and tests before UI exposure.

**Implemented:** authenticated barangay-scoped patient entry, history, search, filtering, sorting, editing, case outcomes, server-side ownership checks, audit logging, schema migration, legacy-record backfill, and private aggregate dashboard counts without patient map markers.

### Phase 5 — support and polish

Add Help/support, onboarding, themes, responsive fixes, accessibility pass, failure-state pass, and full regression validation.

**In progress:** responsive Help and Support page, feature guidance, safe problem-report preparation, submission confirmation, administrator-contact guidance, public and authenticated navigation, plus persistent dark/light themes across all routes. Onboarding, final accessibility/failure-state passes, and full regression validation remain pending.

## Open decisions before implementation

- Which user roles may create, view, edit, export, and delete patient records?
- Is patient name required, or should the system use a non-identifying case ID?
- What retention, consent, audit, backup, and deletion rules apply?
- Which official health pages are approved sources?
- Should pins be local per browser/user or shared through backend storage?
- Should map export use browser canvas capture or server-generated images?
- Which prediction variables are available and how should contribution percentages be calculated?
- Should forecast history be stored in SQLite or retained as immutable run artifacts?
- Which notification channels are in scope?
- Which device/browser versions must be supported?
