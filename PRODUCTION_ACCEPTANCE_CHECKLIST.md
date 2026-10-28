# SYNPORA production acceptance checklist

This checklist is the release gate for the live Railway deployment. A green CI run proves the tested code path and build; it does not by itself prove delivery to a mailbox, long-term feed reliability, persistence after restart, or business-model accuracy.

## Live release status — 2026-10-28

**Status: production candidate; full business acceptance is not yet complete.** Read the exact live commit SHA and deployment ID from Railway's latest successful production deployment rather than hard-coding them in this checklist; the checklist itself changes on release. At the latest post-deploy verification, the application was online and Railway reported no active warnings, critical issues, recent failed deployments, or pending deployment work. The latest merged application changes passed the SYNPORA CI suite, browser smoke/XSS regression, isolated PostgreSQL recovery test, and CodeQL security analysis before deployment.

**Post-deploy smoke:** Railway request logs confirmed successful responses for `/health`, `/`, `/ops`, `/api/v1/system/release`, `/api/v1/system/production-readiness`, `/api/v1/market/data-health`, `/api/v1/system/collector-status`, `/verify-email`, and `/reset-password`. The unauthenticated `POST /api/v1/system/email-connection-test` returned 403 as required. Requests probing `.env` and `.git` paths returned 404. In the last measured 24-hour window before the latest deploy, Railway recorded 1,308 requests with 0 HTTP 5xx responses; a sample of 4xx requests consisted of common automated probes for `.env`, `.git`, WordPress, and legacy admin paths. Continue monitoring rather than treating 404 responses as evidence of a compromise.

**Market-data status:** Global readiness and site-specific learning are separate. The latest detailed fallback-monitor log before the release reported `mode=recommendation_only`, `collector_quality=live`, `readiness=ready`, and market-data age under five minutes. The app's post-deploy `/api/v1/market/data-health` endpoint returned HTTP 200, but the response body was not independently inspected in this deployment check. A fresh generic readiness result is not proof of sustained provider reliability or site profitability. A verified site-specific electricity tariff has not been supplied, so site-tariff eligibility and owner-specific economics remain unvalidated.

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
