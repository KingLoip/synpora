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

`server.py` starts a background collector that attempts to store a market snapshot every 15 minutes when `SYNPORA_MARKET_ADMIN_TOKEN` is configured. It makes the request through the local API and does not need a separate scheduler. Collection failures must be diagnosed using service logs and `/api/v1/market/data-health`; a stored snapshot containing reference values is not eligible for learning or backtesting.

## Deployment checks

1. Confirm the service uses the repository's root `Dockerfile` and `railway.toml`.
2. Wait for the deployment to complete and verify `/health` returns HTTP 200. This is a liveness check only.
3. Open `/ops` and verify `/api/v1/system/production-readiness` reports `status: "ready"`, `database_backend: "postgresql"`, `database_reachable: true`, `jwt_secret_strong: true`, and `market_admin_token_strong: true`. A configured feed must also report `market_data_feed_host_allowlisted: true`; if the provider needs authentication, confirm `market_data_feed_api_key_configured: true`.
4. Register a test account, log in, create/read a farm, and verify data persists after a service restart.
5. Confirm protected market maintenance endpoints reject requests without the admin token.
6. Confirm decision, forecast, and dispatch responses remain recommendation-only and report hardware writes disabled.
7. Check `/api/v1/market/data-health`; confirm the latest eligible snapshot is fresh before trusting model learning.
8. After sufficient verified external observations have accumulated, run `POST /api/v1/farms/{farm_id}/backtest` with `{"min_samples":24,"max_snapshots":1000}` and inspect regret, prediction error, and strategy-selection accuracy.
9. Review `GET /api/v1/farms/{farm_id}/paper-trading`; keep the system in observation mode until enough settled decisions exist and results have been reviewed.

See `SYNPORA_VALIDATION.md` for the provenance rules, endpoint semantics, and limitations. Do not treat an `insufficient_data` backtest as a failure of the service; it means there are not yet enough eligible external observations to make a meaningful evaluation.

A successful GitHub Actions run validates code and image build, but does not prove the Railway service has the required variables or that its PostgreSQL database is reachable. Do not describe the deployment as production-ready until the live readiness and persistence checks pass.
