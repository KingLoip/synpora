# Railway deployment checklist

SYNPORA is recommendation-only. Keep `hardware_write=false` and `autonomous_control=false`; this service must not issue hardware commands.

## Required Railway variables

Configure these in the Railway service's Variables tab. Never commit the secret values to Git.

- `DATABASE_URL`: reference the Railway PostgreSQL service's connection URL. Railway deployments disable the SQLite fallback; a missing or unreachable PostgreSQL database must fail closed.
- `SYNPORA_JWT_SECRET`: a randomly generated secret with at least 32 characters. Changing it invalidates existing login tokens.
- `SYNPORA_MARKET_ADMIN_TOKEN`: a separate, randomly generated secret with at least 32 characters. It protects market collection, backfill, and manual snapshot endpoints.
- `SYNPORA_VAST_API_KEY` (optional): your Vast.ai API key. When configured, SYNPORA samples live L40S marketplace offers and uses the median per-GPU hourly rate. Without it, GPU pricing remains a reference value unless the custom feed provides a verified value.
- `SYNPORA_MARKET_DATA_URL` (optional): HTTPS endpoint for a verified external market feed. It must return JSON with a recent Unix `timestamp` (seconds or milliseconds) and fields `btc_hashprice_usd_ph_day`, `gpu_l40s_usd_hour`, `eur_usd`, and `austria_spot_eur_kwh`, plus optional `gpu_l40s_power_kw`, `gpu_utilization`, and `gpu_platform_fee`. Field aliases in camelCase are also accepted. Values older than 15 minutes, invalid values, and non-HTTPS URLs are rejected. Only fields actually provided and validated by this feed receive its provenance.
- `SYNPORA_MARKET_DATA_ALLOWED_HOSTS` (required when a custom feed URL is configured): comma-separated exact hostnames allowed to serve the feed, e.g. `market-provider.example`. Do not include schemes, paths, credentials, or wildcards. The host must be public; unsafe/private DNS targets and HTTP redirects are rejected. A URL not on this list is ignored.
- `SYNPORA_MARKET_DATA_API_KEY` (optional): API key sent as `Authorization: Bearer …` to the configured feed. Store the key only in Railway Variables, never in the URL or repository.
- `PORT`: provided by Railway; do not hard-code it.

Use distinct values for the JWT secret and market admin token. Do not paste either secret into issues, logs, or chat.

## Operations dashboard

Open `/ops` on the deployed service for a live operational overview of production readiness, data freshness/eligibility, warnings, release safety flags, and the background collector's latest attempt/success/failure. It refreshes every 30 seconds and never displays secret values. The collector's in-memory status resets on service restart; the latest stored snapshot timestamp remains in the database-backed `/api/v1/market/data-health`. The raw JSON is available at `/api/v1/system/production-readiness`, `/api/v1/market/data-health`, and `/api/v1/system/collector-status`.

## Built-in market sources

- Bitcoin hashprice and network indicators: Startmining's public market API.
- EUR/USD: Frankfurter's ECB-provider daily exchange rate; the source date is checked and the value is accepted for up to 96 hours to accommodate weekends.
- Austrian day-ahead spot electricity: Fraunhofer ISE Energy-Charts current-interval endpoint for bidding zone `AT`; the interval's validity is checked. Energy-Charts data is CC BY 4.0 and should be attributed when republished.
- GPU L40S rental rate: median of current Vast.ai marketplace offers when `SYNPORA_VAST_API_KEY` is configured. It is an indicative marketplace rate, not a guaranteed contract price; storage, bandwidth, downtime, and workload performance can change realized revenue.

Every source has its own freshness/provenance checks. If a provider is unavailable, stale, or incomplete, SYNPORA falls back to reference values for display but those reference values do not qualify snapshots for learning or backtesting. The custom aggregator feed is optional and can supplement or override individual fields.

## Market collection

`server.py` starts a background collector that attempts to store a market snapshot every 15 minutes when `SYNPORA_MARKET_ADMIN_TOKEN` is configured. It makes the request through the local API and does not need a separate scheduler. Failed attempts retry with bounded exponential backoff (starting at 60 seconds and capped at 15 minutes); the collector status exposes the last attempt duration, next attempt time, retry delay, and consecutive failure count without exposing exception messages or secrets. The in-memory heartbeat resets on restart; use `/api/v1/market/data-health` to verify persisted snapshots. A stored snapshot containing reference values is not eligible for learning or backtesting.

## Deployment checks

1. Confirm the service uses the repository's root `Dockerfile` and `railway.toml`.
2. Wait for the deployment to complete and verify `/health` returns HTTP 200. This is a liveness check only.
3. Open `/ops` and verify `/api/v1/system/production-readiness` reports `status: "ready"`, `database_backend: "postgresql"`, `database_reachable: true`, `database_schema_ready: true`, `jwt_secret_strong: true`, and `market_admin_token_strong: true`. The SaaS account/farm/asset tables use a dedicated namespace so they cannot collide with legacy ORM tables. A configured feed must also report `market_data_feed_host_allowlisted: true`; if the provider needs authentication, confirm `market_data_feed_api_key_configured: true`.
4. Register a test account, log in, create/read a farm, and verify data persists after a service restart.
5. Confirm protected market maintenance endpoints reject requests without the admin token.
6. Confirm decision, forecast, and dispatch responses remain recommendation-only and report hardware writes disabled.
7. Check `/api/v1/market/data-health`; confirm the latest eligible snapshot is fresh before trusting model learning.
8. After sufficient verified external observations have accumulated, run `POST /api/v1/farms/{farm_id}/backtest` with `{"min_samples":24,"max_snapshots":1000}` and inspect regret, prediction error, and strategy-selection accuracy.
9. Review `GET /api/v1/farms/{farm_id}/paper-trading`; keep the system in observation mode until enough settled decisions exist and results have been reviewed.

See `SYNPORA_VALIDATION.md` for the provenance rules, endpoint semantics, and limitations. Do not treat an `insufficient_data` backtest as a failure of the service; it means there are not yet enough eligible external observations to make a meaningful evaluation.

A successful GitHub Actions run validates code and image build, but does not prove the Railway service has the required variables or that its PostgreSQL database is reachable. Do not describe the deployment as production-ready until the live readiness and persistence checks pass.


## Security and load guardrails

- Authentication rejects malformed/expired JWT claims, enforces an 8–1024 character password range, and performs a dummy password-hash check for unknown login accounts.
- Login throttling and recovery-email request limits use PostgreSQL-backed counters, so limits persist across restarts and are shared by replicas; local in-process login throttling is an additional guard.
- Calculation inputs reject non-finite/extreme numbers, cap Monte Carlo work to 10,000 samples/scenarios, and constrain random seeds to signed 32-bit integers.
- Asset creation validates name, kind (GPU, BTC, ASIC) and nonnegative bounded power; access is scoped to the owning farm. Market history requests are capped at 500 rows.
- Password-reset increments a per-user token version, revoking previously issued JWTs. Keep hardware_write=false and autonomous_control=false; no API in this deployment is authorized to send hardware commands.

## Release acceptance checks

1. GitHub Actions SYNPORA CI passes both the syntax/API/Docker job and the PostgreSQL integration job, including email verification, single-use password reset, and session revocation.
2. Railway reports the newest synpora deployment as SUCCESS, one running replica, and no unresolved critical issues; PostgreSQL is online with its persistent volume attached.
3. Open /health, /ops, /api/v1/system/production-readiness and /api/v1/market/data-health; readiness alone does not prove the live market feed is fresh or economically accurate.
4. Confirm `market_data_ready: true`, a recent successful collector attempt, and eligible snapshots with external provenance for all required market fields. The readiness endpoint now remains blocked when no fully verified snapshot younger than 15 minutes exists. Investigate repeated provider failures or stale data before using forecasts.
5. Treat all rankings as recommendations, not guaranteed returns. Site installation/network costs and user-supplied tax/capex assumptions still need real-world review.


## Email delivery, verification and password recovery

The API now includes one-time email verification and password-reset token flows. Token values are never stored in plaintext; only SHA-256 digests are persisted. Verification links expire after 60 minutes; password-reset links expire after 30 minutes and are single-use. Reset-request and resend-verification responses do not disclose whether an account exists.

Configure these Railway service variables before treating the SaaS authentication flow as production-ready:

- `SYNPORA_SMTP_HOST`
- `SYNPORA_SMTP_PORT` (587 for STARTTLS or 465 for implicit TLS)
- `SYNPORA_SMTP_USERNAME`
- `SYNPORA_SMTP_PASSWORD` (secret)
- `SYNPORA_SMTP_FROM`
- `SYNPORA_PUBLIC_URL` (the canonical HTTPS origin, e.g. the Railway domain)
- `SYNPORA_REQUIRE_EMAIL_VERIFICATION=1`

The production-readiness endpoint reports whether required settings are present; it does not verify the provider credentials or send a test email. After configuration, call `POST /api/v1/system/email-connection-test` with the `X-SYNPORA-MARKET-TOKEN` header. This admin-only diagnostic checks SMTP/TLS/authentication without sending a message and exposes only the error class. Then complete a real delivery/verification/reset smoke test with a controlled mailbox. If these variables are missing, readiness intentionally remains `configuration_required` rather than claiming full production readiness.
