# SYNPORA validation and paper-trading guide

## What is implemented

- `GET /api/v1/market/data-health` reports stored snapshot counts, latest source/provenance, age of the latest eligible observation, last stored snapshot age, per-field provenance, and why data is excluded.
- `GET /api/v1/system/collector-status` reports the background collector's last attempt, last successful collection, last error class, last data-quality status and consecutive failures. The in-memory heartbeat resets on restart; use the database-backed snapshot timestamp in `data-health` to confirm persisted history.
- `POST /api/v1/farms/{farm_id}/backtest` performs strict walk-forward evaluation. At time t it chooses the highest modeled value using snapshot t, then scores that choice against the next later eligible snapshot. It never trains or scores on a snapshot that was not fully externally sourced.
- `GET /api/v1/farms/{farm_id}/paper-trading` summarizes recommendation-ledger outcomes, prediction error, regret, open decisions and per-strategy results. It does not place orders or control equipment.
- `GET /ops` is a browser-based operations dashboard showing readiness, feed freshness, eligible/excluded snapshots, and recommendation-only release flags.
- Scenario, risk-analysis and decision-engine responses expose `cost_model` assumptions for equipment capex, lifetime, maintenance, uptime, pool fee and user-supplied tax rate.
- Existing forecast fitting, model weights and learning settlement use the same provenance gate.

All farm endpoints require a valid bearer token and ownership of the farm. The market data health endpoint contains only service-level market-data diagnostics.

## Eligibility requirements

Global market readiness and site-specific learning are separate gates.

A globally market-ready snapshot requires fresh, finite, explicitly external values for these three fields:

- `btc_hashprice_usd_ph_day`
- `gpu_l40s_usd_hour`
- `eur_usd`

Site-specific model fitting and snapshot-priced benchmarks additionally require a verified, time-aligned `site_energy_cost_eur_kwh` value for the actual farm tariff. The legacy `austria_spot_eur_kwh` field is reference-only for compatibility and must never be silently treated as a site tariff or international training label.

A walk-forward backtest may alternatively accept an explicit constant `energy_cost_eur_kwh` supplied by the operator. Its result is conditional on that assumption; it does not verify the real site's tariff. Reference-only and legacy observations are excluded from training/backtesting.

## Suggested validation sequence

1. Check `GET /api/v1/market/data-health`. Do not expect a backtest to be considered evaluated until enough eligible consecutive observations exist.
2. Call `POST /api/v1/farms/{farm_id}/backtest` with `{"min_samples":24,"max_snapshots":1000}`.
3. Review `metrics.prediction_mae_eur_kwh`, `metrics.mean_regret_eur_kwh`, `metrics.strategy_selection_accuracy`, and `benchmark_by_strategy`.
4. Check `GET /api/v1/farms/{farm_id}/paper-trading` regularly. At least 24 settled recommendations are required before its status becomes `evaluation_available`; this is a minimum reporting threshold, not proof of statistical reliability.
5. Use a longer holdout period before changing model weights or considering any operational automation.

## Forecast-error and risk calibration diagnostics

The independent GitHub Actions economic validation now reports, for both the production parametric comparator and the empirical paired bootstrap:

- Forecast MAE and P90 absolute error in EUR/kWh.
- A same-strategy persistence baseline MAE and MAE skill score. A positive skill score means the model beat carrying forward the current snapshot value; a negative score means it did worse.
- Mean, P90, P95 and maximum regret, plus strategy-selection accuracy.
- A Brier score for the empirical bootstrap's probability that the selected strategy wins.
- A fixed risk-aversion sensitivity sweep (alpha 0.00, 0.25, 0.50, 0.75 and 1.00), comparing realized next-snapshot regret and selection accuracy.

Only consecutive observations no more than 60 minutes apart are paired for the one-step forecast comparison. Longer gaps are counted and excluded rather than silently treated as equivalent horizons. The risk-aversion sweep is diagnostic only: it does not tune or alter production decision-engine settings, and its same-sample best result must not be treated as an independently validated optimum. All results remain market mark-to-model, not realized cashflow.

## Important limitations

- The collector uses Startmining for Bitcoin market indicators, Frankfurter's ECB daily EUR/USD rate, and external GPU rental-price providers (Zarobit, Lium public nodes, and operator-keyed Vast.ai). Austrian spot electricity is no longer fetched as a global market input; the legacy Austria field remains reference-only. Global market readiness does not prove site-specific profitability. A verified site tariff is still required for site-specific learning; a backtest may use an explicitly supplied constant tariff and must label the result as assumption-based.
- Built-in adapters use Startmining for Bitcoin market indicators, Frankfurter's ECB daily EUR/USD rate (up to 96 hours old), and external L40S pricing from Zarobit, Lium public nodes, or Vast.ai when configured. Austrian spot electricity is no longer fetched as a global input; the legacy Austria field is reference-only.
- Set `SYNPORA_VAST_API_KEY` to sample current L40S offers from Vast.ai; SYNPORA uses the median per-GPU hourly rate, not a guaranteed realized revenue. Without the key, GPU prices remain reference values unless a verified custom feed supplies them.
- Operators can configure `SYNPORA_MARKET_DATA_URL` to an HTTPS JSON endpoint and must set `SYNPORA_MARKET_DATA_ALLOWED_HOSTS` to an exact comma-separated hostname allowlist. Private/non-public DNS targets and redirects are rejected. An optional `SYNPORA_MARKET_DATA_API_KEY` is sent as a Bearer token. The feed must provide a recent Unix `timestamp` (seconds or milliseconds) and verified values for the three global fields `btc_hashprice_usd_ph_day`, `gpu_l40s_usd_hour`, and `eur_usd` to support global readiness. Data older than 15 minutes is rejected. A verified `site_energy_cost_eur_kwh` may also be supplied for site-specific history; `austria_spot_eur_kwh` is optional legacy compatibility data and is not a substitute for a site tariff. Provider configuration alone does not prove that the endpoint returns trustworthy data; monitor `data-health` and field-level provenance.
- Risk analysis now includes P05, a lower-tail expected-shortfall metric, probability of negative outcomes, and four deterministic stress cases. `risk_gate` is a warning based on simulated assumptions, not a safety guarantee.
- A successful API response or CI test does not prove that a live provider is available, that Railway secrets are set, or that production PostgreSQL persists data after restart.
- Scenario economics accepts hardware capex (`gpu_capex_eur`, `btc_capex_eur`), expected life in years, annual maintenance in EUR, uptime, pool fee and an operator-entered `tax_rate` from 0 to 1. Capital and maintenance are spread over calendar energy capacity; uptime reduces realized revenue and consumed energy. Review `cost_model` in each response. These fields default to zero for capex, maintenance and tax for backward compatibility, so a realistic comparison requires supplying your own figures. The tax input is not tax advice.
- Backtesting measures historical simulated strategy values using standard variable-margin assumptions; historical market snapshots do not contain each user's own capex, maintenance, tax, contract availability or site constraints. Backtests therefore do not automatically represent full owner-specific lifetime profitability.
- One-off installation costs, network/site import limits, provider contract availability and Austrian tax treatment are not inferred automatically. They must be checked separately before making an investment decision.
- Keep `hardware_write=false` and `autonomous_control=false`. These APIs are recommendation and observation tools only.


## Additional hardening (2026-10-10)

- JWT payload claims are type-checked; malformed, expired, non-finite-expiry, empty, and oversized user identifiers are rejected.
- Login uses a dummy PBKDF2 verification for unknown accounts to reduce account-enumeration timing differences. Failed login throttling is process-local (five failures per normalized email in a 15-minute window trigger a five-minute lockout); it resets on restart and is not a distributed rate limiter.
- Registration validates basic email structure and limits passwords to 8–1024 characters. This is input hardening, not email ownership verification or a password-reset workflow.
- Numeric calculations reject non-finite values and magnitudes above 1e12. Monte Carlo sample/scenario counts are limited to 1–10,000 and seeds to signed 32-bit integers to cap work per request.
- Asset writes accept only GPU, BTC, or ASIC kinds, require a non-empty name of at most 120 characters, reject negative or extreme power values, and verify farm ownership before reading or writing assets.
- Market history limits are clamped to 1–500 and invalid historical JSON payloads are returned as marked invalid records rather than crashing the endpoint.

These controls reduce common abuse and robustness risks but do not replace distributed rate limiting, email verification, password reset, a formal penetration test, or monitoring of real traffic.


## Account security and production email

- Production deployments require email verification by default; registration fails closed before creating an account if SMTP is not configured.
- Configure SMTP host, port, username, password, sender, and the canonical HTTPS public URL. Password-reset requests and verification resends return generic responses to avoid account enumeration and are rate-limited using PostgreSQL-backed counters.
- Use the admin-only `POST /api/v1/system/email-connection-test` endpoint with the market-maintenance token to test SMTP connectivity and authentication. It does not send email.
- Verification and password-reset tokens are random, stored only as SHA-256 digests, expire, and are single-use. A successful password reset increments the user's token version and invalidates previously issued JWTs.
- The production-readiness endpoint requires a recent fully provenance-verified market snapshot, persistent PostgreSQL, strong secrets, and configured email delivery. `ready` does not imply independent security certification or proven economic forecast performance.


Shared account-level throttling reserves each login/registration/reset attempt in a row-locked PostgreSQL transaction before expensive authentication or email work. This avoids a concurrent check-then-increment race; the PostgreSQL CI job includes a parallel-request regression test.
