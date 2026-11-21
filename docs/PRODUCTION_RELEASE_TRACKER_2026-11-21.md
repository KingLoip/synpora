# SYNPORA production release tracker — 2026-11-21

This tracker separates **technical deployment readiness** from **empirical forecast validation** and **investment/business acceptance**. A successful build or a fresh market snapshot must never be described as proof of forecast skill or profitability.

## Verified repository evidence

Latest checked PR #83 head: `062b4c943633cc105c2e37f2f35a14ea492aa99d`.

- SYNPORA CI: passed (syntax, provider fallback tests, imports, API E2E, JWT/auth regression, dashboard, Docker build, release invariants, PostgreSQL integration).
- CodeQL: passed.
- Browser smoke/XSS regression: passed.
- Isolated PostgreSQL restart and logical backup/restore test: passed.
- Economic validation workflow executed successfully as a test harness but returned a **blocked forecast release gate**. This is an intentional fail-closed result, not a CI infrastructure failure.
- Live production smoke: skipped; no new deployment or production mutation was performed.

## Latest strict forecast evidence

Across the four illustrative international cost scenarios, the empirical joint BTC+GPU gate reports:

| Horizon | Genuine joint non-overlapping OOS pairs | Status |
|---|---:|---|
| 1h | 11 | Blocked: fewer than 100, BTC selector fallbacks, lower 95% bootstrap bound not positive |
| 6h | 0 | Blocked: no qualifying joint pairs |
| 24h | 0 | Blocked: insufficient separated history and selector fallbacks |
| 72h | 0 | Blocked: insufficient separated history and selector fallbacks |

BTC independent 1h evidence: 46 pairs, +1.401% MAE skill, lower 95% CI -20.660%. GPU independent 1h evidence: 196 pairs, +27.005% MAE skill, lower 95% CI +14.471%. GPU-only success cannot replace the joint BTC+GPU requirement.

The gate remains unchanged: at least 100 genuine non-overlapping joint OOS pairs per horizon, at least 5% MAE skill versus persistence, a positive lower 95% block-bootstrap confidence bound, and no strategy falling back to persistence in counted pairs. All four horizons must pass.

## Data-volume runway — not a release-date promise

The validator's own history projection estimates, at the observed collection cadence, approximately:
- 24h horizon: 70.33 additional days to reach the data-volume lower bound for 100 non-overlapping OOS pairs.
- 72h horizon: 280.09 additional days to reach that lower bound.

These are **data-only lower bounds**, not forecasts of model success. They assume uninterrupted eligible observations and do not guarantee candidate selection, 5% skill, positive confidence bounds, or successful joint BTC+GPU validation. The 72h horizon is currently the longest-pole constraint. BTC selector quality may take longer or may never meet the gate without a better model/data source.

## Separate release tracks

### A. Technical platform release
Can be considered independently of the forecast-validation gate only if the product is explicitly limited to recommendation/observation mode and every applicable operational acceptance item is evidenced. Current open items include:
- Restore/replace the unavailable legacy model registry route; the current protective API guard returns a controlled 503 `model_registry_unavailable` rather than a server error.
- Run a live production smoke against the exact deployed commit and inspect endpoint response contracts.
- Confirm controlled end-to-end email verification delivery and one-time token behavior.
- Verify production PostgreSQL persistence across a controlled application restart and perform a production backup/restore drill.
- Observe sustained collector freshness and operational error/latency behavior.
- Verify the Railway/Cloudflare trusted proxy chain before trusting forwarded client-IP headers.

### B. Validated forecasting release
Remain blocked until all four empirical horizon gates pass on fresh evidence. Keep dispatch `unvalidated`/ `degraded` and recommendation-only. Do not relax thresholds or count persistence fallbacks.

### C. Investment/business validation
Separate from market backtests. Requires verified settled GPU payouts, BTC pool settlements, all-in metered energy and export remuneration, capex, maintenance, uptime and site-specific operating costs. Until then report `not_investment_validated`.

## Deployment safety

PRs remain drafts. Do not merge stacked PR #83 before its base PR #82. No production deployment, live database mutation, hardware write, autonomous control, trade, or wallet action is authorized by this tracker. Keep `hardware_write=false` and `autonomous_control=false`.
