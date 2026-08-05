# Model Validation

## Current model position

Engine uses historical profiles, spatial/temporal effects, Gamma-Poisson Bayesian simulation, and weighted-ensemble-derived municipality calibration factors.

Weighted ensemble artifact contains MLR, SARIMAX, LSTM, and XGBoost weights. Current runtime does not execute/retrain those four models; it uses calibration factors derived from stored test prediction table.

## Scientific limits

- monthly targets are synthetic/interpolated
- long horizon through 2050 magnifies uncertainty
- current test metrics cannot establish clinical/operational readiness
- label all outputs as scenarios until real-world validation passes

## Validation sequence

1. Obtain approved observed monthly barangay surveillance data.
2. Freeze raw data and create provenance record.
3. Define time-based train/validation/test split.
4. Compare against naive seasonal baseline.
5. Measure MAE, RMSE, WAPE, calibration, false-alert rate, missed-alert rate, and lead time.
6. Assess results per municipality and season, not only overall.
7. Validate alert thresholds with domain experts.
8. Publish model card and limitations.
9. Require approval before operational rollout.

## Questions needing answer

- Forecast target: cases, incidence rate, or alert probability?
- Decision horizon: 1 month, 3 months, or longer?
- What qualifies as outbreak ground truth?
- What false-positive rate is tolerable?
- How often will model/config be reviewed?

## Weather data decision

Collect and version approved Open-Meteo weather history for future validation. Do not claim weather influences current runtime forecasts until weather features are defined, models are retrained, leakage is checked, and evaluation passes.

## Region XII expansion gates

All required:

1. South Cotabato pilot accepted.
2. Official comparable data available for all target provinces.
3. Municipality-level model passes agreed validation and alert criteria.

## Output requirements

Every run needs:

- dataset version/hash
- model artifact version/hash
- configuration snapshot
- geography/boundary version
- evaluation period and metrics
- scientific limitation text
