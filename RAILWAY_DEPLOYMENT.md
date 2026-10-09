# Railway deployment checklist

SYNPORA is recommendation-only. Keep `hardware_write=false` and `autonomous_control=false`; this service must not issue hardware commands.

## Required Railway variables

Configure these in the Railway service's Variables tab. Never commit the secret values to Git.

- `DATABASE_URL`: reference the Railway PostgreSQL service's connection URL. Railway deployments disable the SQLite fallback; a missing or unreachable PostgreSQL database must fail closed.
- `SYNPORA_JWT_SECRET`: a randomly generated secret with at least 32 characters. Changing it invalidates existing login tokens.
- `SYNPORA_MARKET_ADMIN_TOKEN`: a separate, randomly generated secret with at least 32 characters. It protects market collection, backfill, and manual snapshot endpoints.
- `PORT`: provided by Railway; do not hard-code it.

Use distinct values for the JWT secret and market admin token. Do not paste either secret into issues, logs, or chat.

## Deployment checks

1. Confirm the service uses the repository's root `Dockerfile` and `railway.toml`.
2. Wait for the deployment to complete and verify `/health` returns HTTP 200. This is a liveness check only.
3. Verify `/api/v1/system/production-readiness` reports `status: "ready"`, `database_backend: "postgresql"`, `database_reachable: true`, `jwt_secret_strong: true`, and `market_admin_token_strong: true`.
4. Register a test account, log in, create/read a farm, and verify data persists after a service restart.
5. Confirm protected market maintenance endpoints reject requests without the admin token.
6. Confirm decision, forecast, and dispatch responses remain recommendation-only and report hardware writes disabled.

A successful GitHub Actions run validates code and image build, but does not prove the Railway service has the required variables or that its PostgreSQL database is reachable. Do not describe the deployment as production-ready until the live readiness and persistence checks pass.
