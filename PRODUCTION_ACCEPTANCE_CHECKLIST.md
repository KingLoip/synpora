# SYNPORA production acceptance checklist

This checklist is the release gate for the live Railway deployment. A green CI run proves the tested code path and build; it does not by itself prove delivery to a mailbox, long-term feed reliability, persistence after restart, or business-model accuracy.

## Live release status — 2026-10-13

**Not fully production-ready yet.** The latest successful live smoke test (2026-10-13 around 16:40 UTC) observed `status: ready`; the GPU field was external (`Zarobit L40S on-demand listing median`, observation age 318.4 seconds), and the latest eligible snapshot age was 298.3 seconds. CI's syntax, PostgreSQL integration, live smoke and CodeQL jobs passed for the latest checked commit. PostgreSQL and security/configuration checks also passed; safety flags remained off (`hardware_write=false`, `autonomous_control=false`).

This is a successful point-in-time recovery, not proof of sustained provider reliability. Earlier collector cycles had six Zarobit L40S offers but zero fresh offers, and one cycle reported no fresh Austrian Energy-Charts observation. No Vast.ai key or custom feed was configured at the time of the successful smoke test, so the built-in Zarobit feed briefly supplied eligible data. Keep monitoring repeated collector cycles and confirm the latest eligible snapshot stays within 900 seconds before calling the service fully production-ready.

**Next operational action:** no secret is needed if the built-in feed continues to provide fresh external prices. If it becomes stale again, configure a legitimate `SYNPORA_VAST_API_KEY` in Railway Variables or a controlled HTTPS feed with an exact hostname allowlist. Never paste secrets into chat or GitHub. See [GPU market-data recovery runbook](GPU_MARKET_DATA_RECOVERY.md).

## Safety invariants — must always pass

- [ ] Release endpoint reports `mode: recommendation_only`.
- [ ] `hardware_write=false` and `autonomous_control=false`.
- [ ] No production release is marked fully production-ready while any acceptance gate below is unverified.
- [ ] Secrets are stored only in Railway Variables. Never paste them into GitHub issues, commits, logs, or chat.

## Automated checks

- [ ] GitHub Actions syntax, import, API end-to-end, JWT/auth regression, dashboard, Docker build and release invariant checks pass.
- [ ] PostgreSQL integration tests pass, including persistence and repeatable/idempotent market migrations.
- [ ] CodeQL finishes successfully.
- [ ] Live production smoke test sees the expected commit on the public Railway URL.
- [ ] `/health`, `/api/v1/system/production-readiness`, `/api/v1/market/data-health` and `/ops` return their expected contracts.
- [ ] `/api/v1/system/collector-status` exposes attempt/success timestamps, quality, failure count and bounded retry timing; degraded data must not advance the last-success timestamp.
- [ ] The unauthenticated `POST /api/v1/system/email-connection-test` is rejected (403); this endpoint must remain protected.

## Live data quality and learning eligibility

- [ ] Production readiness reports PostgreSQL configured, reachable and schema-ready.
- [ ] Global market readiness requires explicit external provenance, valid finite values, and source-appropriate freshness for hashprice (15 minutes), GPU rental prices (15 minutes), and EUR/USD (96 hours).
- [ ] Austrian/aWATTar electricity pricing is not a global production requirement; energy cost must be supplied for the actual mining/compute site or tariff.
- [ ] Global market readiness reports a dedicated latest-ready snapshot age no greater than 900 seconds; this is distinct from learning eligibility.
- [ ] Learning/backtesting remains blocked unless the energy-cost observation used to settle a decision is verified, time-aligned, and appropriate to that site's tariff. Legacy Austrian observations must not be silently treated as international/site-specific training labels.
- [ ] Reference/fallback values remain clearly distinguished and are excluded from learning/backtesting.
- [ ] Review the collector status for repeated failures and confirm snapshots continue to arrive over time. A single fresh snapshot is not evidence of durable feed reliability.
- [ ] If GPU rental prices need to be live rather than reference estimates, configure a provider API key or an HTTPS custom feed with an exact host allowlist; do not weaken freshness limits to make readiness pass.

## Email — configuration is not delivery proof

- [ ] Production readiness reports SMTP configured and email verification required.
- [ ] Run the protected SMTP connection diagnostic using the admin token from a trusted operator environment; do not expose the token in browser history, logs or chat.
- [ ] Confirm the diagnostic reports a successful secure connection/authentication and sends no message.
- [ ] Separately perform one explicitly authorized end-to-end verification-email test to a controlled mailbox.
- [ ] Confirm receipt, valid HTTPS link, successful one-time verification, expiration behavior, and that tokens are not logged or cached.
- [ ] If the message is missing, check provider delivery events, spam/quarantine and sender-domain SPF/DKIM/DMARC. Do not treat a successful SMTP handshake as proof of inbox placement.

## Persistence and user workflow

- [ ] Register a dedicated test account and verify duplicate registration is rejected safely.
- [ ] Verify login, email verification, password reset and one-time token consumption.
- [ ] Create/read a farm and asset; verify another account cannot access them.
- [ ] Verify account, farm, asset and market snapshot data persists across a controlled service restart.
- [ ] Verify password reset invalidates prior access tokens and throttling survives restarts via PostgreSQL-backed state.

## Performance and operational monitoring

- [ ] Review Railway service status, deployment logs, HTTP 5xx rate and latency percentiles after deployment.
- [ ] Confirm no repeated crash/restart loop and no unexplained rise in 5xx errors.
- [ ] Review collector last-attempt/last-success timestamps and consecutive failures.
- [ ] Review a sufficiently long operating window; a single smoke test or one-hour zero-error window is only a point-in-time signal.
- [ ] Check database backup/restore procedure and run a restore test before relying on production data.

## Decision engine and model validity

- [ ] Keep recommendations in paper/observation mode until enough verified, settled external observations exist.
- [ ] Run backtests only when eligible sample count is sufficient; record prediction error, regret and strategy-selection accuracy.
- [ ] Treat `insufficient_data` as a lack of evidence, not as a successful model validation.
- [ ] Review the assumptions, fees, downtime, power costs, and market/provider limitations before using financial estimates operationally.

## Release decision

The automated readiness status may be `ready` while business acceptance remains incomplete. Mark the product fully production-ready only after all applicable items above have evidence recorded, including actual mailbox receipt, persistence verification, and an adequate period of live-data/collector observation. Keep the release status at `production_candidate` until then.
