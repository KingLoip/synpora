# Railway deployment checklist

SYNPORA is recommendation-only. Keep `hardware_write=false` and `autonomous_control=false`; this service must not issue hardware commands.

## Required Railway variables

Configure these in the Railway service's Variables tab. Never commit the secret values to Git.

- `DATABASE_URL`: reference the Railway PostgreSQL service's connection URL. Railway deployments disable the SQLite fallback; a missing or unreachable PostgreSQL database must fail closed.
- `SYNPORA_JWT_SECRET`: a randomly generated secret with at least 32 characters. Changing it invalidates existing login tokens.
- `SYNPORA_MARKET_ADMIN_TOKEN`: a separate, randomly generated secret with at least 32 characters. It protects market collection, backfill, and manual snapshot endpoints.
- `SYNPORA_MARKET_DATA_URL` (optional): HTTPS endpoint for a verified external market feed. It must return JSON with a recent Unix `timestamp` (seconds or milliseconds) and any available fields among `btc_hashprice_usd_ph_day`, `gpu_l40s_usd_hour`, `eur_usd`, `austria_spot_eur_kwh`, `gpu_l40s_power_kw`, `gpu_utilization`, and `gpu_platform_fee`. Field aliases in camelCase are also accepted. Values older than 15 minutes, invalid values, and non-HTTPS URLs are rejected. Only the four critical values with accepted external provenance can qualify a snapshot for learning.
- `PORT`: provided by Railway; do not hard-code it.

Use distinct values for the JWT secret and market admin token. Do not paste either secret into issues, logs, or chat.

## Scheduled market collection

The repository includes `.github/workflows/market-collection.yml`, which runs every 15 minutes and can also be started manually. In GitHub, configure repository Actions secrets `SYNPORA_BASE_URL` (the public base URL of the deployed service, without a trailing slash) and `SYNPORA_MARKET_ADMIN_TOKEN` (the same secret configured in Railway). Until both are configured, the workflow safely skips collection. The collector reports provenance quality; snapshots containing reference values remain excluded from learning and backtesting.

## Deployment checks

1. Confirm the service uses the repository's root `Dockerfile` and `railway.toml`.
2. Wait for the deployment to complete and verify `/health` returns HTTP 200. This is a liveness check only.
3. Verify `/api/v1/system/production-readiness` reports `status: "ready"`, `database_backend: "postgresql"`, `database_reachable: true`, `jwt_secret_strong: true`, and `market_admin_token_strong: true`.
4. Register a test account, log in, create/read a farm, and verify data persists after a service restart.
5. Confirm protected market maintenance endpoints reject requests without the admin token.
6. Confirm decision, forecast, and dispatch responses remain recommendation-only and report hardware writes disabled.
7. Check `/api/v1/market/data-health`; confirm the latest eligible snapshot is fresh before trusting model learning.
8. After sufficient verified external observations have accumulated, run `POST /api/v1/farms/{farm_id}/backtest` with `{"min_samples":24,"max_snapshots":1000}` and inspect regret, prediction error, and strategy-selection accuracy.
9. Review `GET /api/v1/farms/{farm_id}/paper-trading`; keep the system in observation mode until enough settled decisions exist and results have been reviewed.

See `SYNPORA_VALIDATION.md` for the provenance rules, endpoint semantics, and limitations. Do not treat an `insufficient_data` backtest as a failure of the service; it means there are not yet enough eligible external observations to make a meaningful evaluation.

A successful GitHub Actions run validates code and image build, but does not prove the Railway service has the required variables or that its PostgreSQL database is reachable. Do not describe the deployment as production-ready until the live readiness and persistence checks pass.
