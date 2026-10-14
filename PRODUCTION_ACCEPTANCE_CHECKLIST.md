# SYNPORA production acceptance checklist

This checklist is the release gate for the live Railway deployment. A green CI run proves the tested code path and build; it does not by itself prove delivery to a mailbox, long-term feed reliability, persistence after restart, or business-model accuracy.

## Live release status — 2026-10-14

**Still a production candidate, not fully production-ready.** The international-readiness change was merged as `03810cf9cf61605e17f2d1c06cd8e54036ff6a5a` and deployed successfully. The bounded provider-retry change was then merged as `0d4366dfdda1827258ef60e6d4fc147666c853cd` and also deployed successfully. PostgreSQL and the application are online; Railway reports no current warnings, critical issues, crashed replicas, or failed deployments.

The PR checks for the latest code passed: syntax/provider unit tests, import/API smoke, JWT regression, dashboard smoke, Docker build, release invariants, PostgreSQL integration, and CodeQL. After deployment, the health, release, production-readiness, data-health, collector-status, and operations routes returned HTTP 200; the unauthenticated SMTP diagnostic correctly returned HTTP 403. Railway recorded **0 HTTP 5xx responses over the last hour**. This is good operational evidence, but not a substitute for checking response payloads or a sustained observation window.

**International data policy:** Austrian/aWATTar prices are no longer a global market-readiness requirement. Energy cost remains essential for profitability, but must be supplied for the actual site/tariff. Forecast plans hold the supplied site tariff constant rather than forecasting from Austrian history. Learning/backtesting is deliberately not considered validated until verified, site-appropriate energy-cost labels are available.

The Austrian Energy-Charts provider still returned HTTP 503 during a collection cycle; older Zarobit listings have also been stale. These regional/provider failures must not be mistaken for global market failure, but they do reinforce the need to verify repeated successful collection cycles and fresh GPU pricing. The latest observed collector POST returned HTTP 200; one cycle alone does not prove durable feed reliability. The post-merge scheduled production-monitor result has not yet been independently confirmed in this checklist.

**Next operational action:** verify the scheduled production monitor and repeated collector freshness. If GPU prices become stale, validate the configured `SYNPORA_VAST_API_KEY` directly in Railway Variables or use a controlled HTTPS feed with an exact hostname allowlist. Never paste secrets into chat or GitHub. See [GPU market-data recovery runbook](GPU_MARKET_DATA_RECOVERY.md).

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
