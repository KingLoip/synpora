# SYNPORA production acceptance checklist

This checklist is the release gate for the live Railway deployment. A green CI run proves the tested code path and build; it does not by itself prove delivery to a mailbox, long-term feed reliability, persistence after restart, or business-model accuracy.

## Live release status — 2026-10-28

**Status: production candidate; full business acceptance is not yet complete.** Railway deployment `45bb0f41-96c4-4ade-b355-be03aec7f488` is live on commit `f69992cc3d012c98275da0a4d85efe80efb130b6`. The application, PostgreSQL service, and scheduled fallback monitor report online/successful, with no active Railway warnings, critical issues, or recent failed deployments. The latest detailed fallback-monitor log (2026-10-28 20:30 UTC) reported `mode=recommendation_only`, `collector_quality=live`, `readiness=ready`, and market-data age 26.1 seconds; Railway reported the next scheduled run succeeded at 20:40 UTC. GitHub CI, browser smoke/XSS regression, isolated PostgreSQL recovery, and CodeQL checks for the Monte Carlo fix's PR commit all completed successfully.

**HTTP reliability and probing:** In the last 24 hours, Railway counted 1,308 requests: 940 2xx, 368 4xx, and 0 5xx. A sample of detailed 4xx logs includes common automated probes for `.env`, `.git`, WordPress, and legacy admin paths, returning 404/405; no 5xx errors were observed. This is consistent with background internet scanning, not proof of a successful compromise. Keep monitoring these requests and preserve the existing secret/config-path protections. The measured 24-hour response latency was low (p50 typically 2–7 ms; worst sampled p95 147 ms and p99 407 ms).

**Market-data status:** Global readiness and site-specific learning are separate. The latest detailed monitor confirms a fresh, globally market-ready collector snapshot, but a single recent snapshot and a generic `ready` flag do not prove sustained provider reliability or site profitability. A verified site-specific electricity tariff has not been supplied, so site-tariff eligibility and owner-specific economics remain unvalidated. Keep reference/fallback values excluded from learning and backtests.

**Business acceptance still open:** no end-to-end verification email has been confirmed received in a controlled mailbox, and production PostgreSQL persistence across a controlled application restart plus a production backup/restore drill have not been independently demonstrated. The isolated PostgreSQL recovery workflow passed in CI, but that is not evidence of a restore from the live production database. Do not mark SYNPORA fully production-ready until applicable gates below have evidence. Keep the product in recommendation-only mode.

## Next operational action

1. Observe repeated scheduled live-monitor runs and collector cycles, and investigate any L40S freshness regression without lowering provenance or age thresholds.
2. Supply a verified site-specific electricity tariff for the farm to evaluate real profitability and site-specific learning; explicit constant-tariff backtests remain assumption-based.
3. Complete an authorized end-to-end email-verification test to a controlled mailbox.
4. Verify production persistence after a controlled application restart and test PostgreSQL backup/restore before relying on production data.

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
