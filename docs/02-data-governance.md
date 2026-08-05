# Data Governance

## Known data limits

`data/ORACLIS_Monthly_Barangay_Data_Corrected.csv` marks dengue values as `SYNTHETIC_INTERPOLATED`. Monthly values were derived from annual totals and barangay population shares.

Therefore:

- current forecasts are scenario outputs
- current model metrics do not prove operational predictive accuracy
- automated messages must not claim confirmed outbreak status

## Live observation contract

Normalized input columns:

| Field | Rules |
|---|---|
| `DATE` | Month, normalized to `YYYY-MM-01` |
| `PSGC` | Exact 10-digit South Cotabato barangay code |
| `OBSERVED_CASES` | Finite non-negative number |
| `EXPOSURE` | Finite non-negative number; default `1` |

Existing validator: `integration/ingest_live_observations.py`.

## Before live data integration

- official owner: Provincial Health Office
- identify source system and data owner
- document extraction schedule and late-correction policy
- map source localities to PSGC once, then review exceptions
- preserve source file hash, received timestamp, and validation result
- define who can upload, correct, and approve data
- define retention policy and backup location

Expected sources may include monthly barangay cases, monthly municipality cases, and annual totals. Monthly barangay data is preferred. Municipality/annual records must not be silently allocated into monthly barangay observations; preserve their original resolution or apply a documented, approved method.

## Correction and update policy

- Runs happen on demand.
- Data manager submits aggregate observations.
- Corrections are versioned; never destroy original value.
- Audit record stores dataset version, old/new value, reason, actor, and timestamp.
- Administrator approves public publication separately from data import.
- Latest run never publishes automatically.

## Privacy and safety

- ingest aggregate barangay/month counts only unless approved governance says otherwise
- do not expose patient identifiers, addresses, free-text case notes, or raw line lists
- separate secrets from source code and generated files
- grant upload/export permissions by role

## Data quality gates

Reject or quarantine data with:

- invalid/out-of-scope PSGC
- duplicate `DATE + PSGC` records without correction policy
- negative or non-finite values
- missing reporting period
- unexplained population/exposure shifts

## Open decisions

- What official data source will replace synthetic history?
- Which Provincial Health Office role approves corrected historical records?
- Is `EXPOSURE` population, reporting completeness, or another denominator?
- What aggregation can leave organization boundary?
