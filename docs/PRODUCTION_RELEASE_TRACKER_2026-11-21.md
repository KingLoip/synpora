# SYNPORA production release tracker — 2026-11-21

This tracker separates **technical deployment readiness** from **empirical forecast validation** and **investment/business acceptance**. A successful build or a fresh market snapshot must never be described as proof of forecast skill or profitability.

## Current production verification

Latest merged production commit: `cbc6b34f8865951fffa0890508840f8db508118b` (PRs #89 and #90). Railway deployment: `6ea9e660-f99e-4ddf-bd87-eb82221db5d5`.

- PR #89 merged international forecast hardening, fail-closed dispatch and economic-validation workflow behavior.
- PR #90 fixed the production Docker packaging omission for `security_path_monitor.py` and added a real container-start smoke test that starts the built image and checks `/health`.
- Main-branch SYNPORA CI, CodeQL, browser/XSS smoke, PostgreSQL integration, and live production smoke passed after the final merge.
- The production API is online with 1/1 running replica; PostgreSQL is online with 1/1 running replica.
- Railway reports 0 active warnings and 0 active critical alerts. One earlier failed deployment remains in the recent-history window; it was superseded by the successful deployment above.
- The fallback monitor's latest run succeeded.
- Railway recorded 1,319 API requests over the last 24 hours and 0 HTTP 5xx responses (0% server-error rate). There were 343 HTTP 4xx responses; these are client-side/probe responses and are not counted as server errors.
- The live production smoke verified HTTP 200 for `/health`, `/api/v1/system/release`, `/api/v1/system/production-readiness`, `/api/v1/market/data-health`, `/api/v1/system/collector-status`, `/ops`, `/verify-email`, and `/reset-password`. The unauthenticated email diagnostic correctly returned 403.

### Known technical limitation — model registry

`/api/v1/models` remains intentionally fail-closed with controlled HTTP 503 `model_registry_unavailable`. The packaged legacy route references missing `list_models` / `select_best` helpers. The guard prevents an externally-triggerable NameError/500, but it is not a substitute for restoring and testing the actual registry. Do not remove the guard or report the route as operational until the real registry is restored and covered by package-level and live endpoint tests.

## Latest strict forecast evidence

Latest completed strict economic validation evidence (2026-11-21) across the four illustrative international cost scenarios:

| Horizon | Genuine joint non-overlapping BTC+GPU OOS pairs | Status |
|---|---:|---|
| 1h | 11 | Blocked: fewer than 100; lower 95% bootstrap bound unavailable |
| 6h | 0 | Blocked: no qualifying joint pairs |
| 24h | 0 | Blocked: insufficient separated history |
| 72h | 0 | Blocked: insufficient separated history |

The 1h joint MAE skill estimate was +46.360%, but the lower 95% bootstrap bound is unavailable at n=11. Independent series do not replace the joint gate: BTC 1h had 46 pairs, +1.401% skill and lower 95% CI -20.660%; GPU 1h had 207 pairs, +29.712% skill and lower 95% CI +17.153%.

The gate remains unchanged: at least 100 genuine non-overlapping joint OOS pairs per horizon, at least 5% MAE skill versus persistence, a positive lower 95% block-bootstrap confidence bound, separated training/holdout origins, and no strategy falling back to persistence in counted pairs. All four horizons must pass.

## Data-volume runway — not a release-date promise

At the observed eligible collection cadence, the validator estimates approximately:
- 24h horizon: 8,315 additional observations / 69.49 days to reach the data-volume lower bound for 100 non-overlapping joint OOS pairs.
- 72h horizon: 33,414 additional observations / 279.25 days to reach that lower bound.

These are **data-only lower bounds**, not forecasts of model success. They assume uninterrupted eligible observations and do not guarantee candidate selection, 5% skill, positive confidence bounds, or successful joint BTC+GPU validation. Increasing collection frequency with repeated cached data does not create independent observations.

## Separate release tracks

### A. Technical platform release

The application is deployed and operational in recommendation/observation mode, but full technical/business acceptance is not complete. Current open items include:
- Restore/replace the unavailable legacy model registry route; the current protective API guard returns a controlled 503 `model_registry_unavailable`.
- Complete an authorized end-to-end verification-email test and confirm actual mailbox receipt plus one-time token behavior. A protected diagnostic returning 403 without authorization is expected and is not email-delivery proof.
- Verify production PostgreSQL persistence across a controlled application restart and perform a production backup/restore drill. The isolated PostgreSQL recovery workflow passed in CI, but that is not evidence of a restore from the live production database.
- Observe sustained collector freshness and operational error/latency behavior over a longer window.
- Verify the Railway/Cloudflare trusted proxy chain before trusting forwarded client-IP headers.

### B. Validated forecasting release

Remain blocked until all four empirical horizon gates pass on fresh evidence. Keep dispatch `unvalidated` / `degraded` and recommendation-only. Do not relax thresholds or count persistence fallbacks.

### C. Investment/business validation

Status remains `not_investment_validated`. Validation requires verified settled GPU payouts, BTC pool settlements, all-in metered energy and export remuneration, capex, maintenance, uptime and site-specific operating costs. None may be replaced by illustrative tariffs or reference prices.

## Deployment safety

- Keep `hardware_write=false` and `autonomous_control=false`.
- No hardware writes, autonomous control, trades, wallet actions, or purchases.
- A green CI run proves only the tested code paths; a live HTTP 200 proves reachability, not economic quality, email delivery, or durable database recovery.
- Keep the model-registry guard in place until a real implementation is restored and tested.
