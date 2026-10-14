# SYNPORA production acceptance checklist

This checklist is the release gate for the live Railway deployment. A green CI run proves the tested code path and build; it does not by itself prove delivery to a mailbox, long-term feed reliability, persistence after restart, or business-model accuracy.

## Live release status — 2026-10-14

**Production candidate; business acceptance is still incomplete.** The current main commit `daa5ce2dc9ad732062d626c03f204ee0f1e3f6be` was deployed successfully to Railway production at approximately 08:59:50 UTC. The application and PostgreSQL were subsequently reported online with no Railway warning/critical issues and no pending deployment work.

Verified evidence for this release:
- [Production CI and live smoke test](https://github.com/KingLoip/synpora/actions/runs/38539016356) passed, including syntax/unit tests, API and dashboard smoke tests, Docker build, release invariants, PostgreSQL persistence/migration integration, and the live production smoke test.
- [CodeQL](https://github.com/KingLoip/synpora/actions/runs/38539016634) passed.
- [Production monitor PR run](https://github.com/KingLoip/synpora/actions/runs/38538892591) passed against production after adding checks for the Railway health endpoint, the canonical `synpora.com` homepage, and `synpora.com/ops`.
- The recurring read-only monitor is merged and configured for a 15-minute schedule. At the time of this checklist update, a post-merge scheduled invocation had not yet been confirmed; the successful monitor run above was triggered by the pull request.
- The monitor enforces PostgreSQL readiness, external provenance and per-field freshness limits, successful/live collector status, zero consecutive collector failures, and the safety invariants `hardware_write=false` and `autonomous_control=false`.

These checks are point-in-time evidence, not proof of sustained provider reliability, actual email delivery, restart persistence in production, backup recoverability, or business-model accuracy. Do not mark the product fully production-ready until the applicable manual gates below have recorded evidence.

**Next operational action:** continue observing scheduled monitor runs and repeated collector cycles. No secret is needed while the built-in feed supplies fresh external prices. If the GPU field becomes stale, configure a legitimate `SYNPORA_VAST_API_KEY` in Railway Variables or a controlled HTTPS feed with an exact hostname allowlist. Never paste secrets into chat or GitHub. See [GPU market-data recovery runbook](GPU_MARKET_DATA_RECOVERY.md).

## Safety invariants — must always pass

- [ ] Release endpoint reports `mode: recommendation_only`.
- [x] `hardware_write=false` and `autonomous_control=false` (verified by the live production smoke test and production monitor on 2026-10-14; see [CI run](https://github.com/KingLoip/synpora/actions/runs/38539016356)).
- [ ] No production release is marked fully production-ready while any acceptance gate below is unverified.
- [ ] Secrets are stored only in Railway Variables. Never paste them into GitHub issues, commits, logs, or chat.

## Automated checks

- [x] GitHub Actions syntax, import, API end-to-end, JWT/auth regression, dashboard, Docker build and release invariant checks pass (2026-10-14; [CI run](https://github.com/KingLoip/synpora/actions/runs/38539016356)).
- [x] PostgreSQL integration tests pass, including persistence and repeatable/idempotent market migrations (2026-10-14; same CI run).
- [x] CodeQL finishes successfully (2026-10-14; [CodeQL run](https://github.com/KingLoip/synpora/actions/runs/38539016634)).
- [x] Live production smoke test sees the expected commit on the public Railway URL (2026-10-14; same CI run).
- [x] The read-only monitor's production PR check validated the Railway health endpoint, `synpora.com/`, and `synpora.com/ops` (2026-10-14; [monitor run](https://github.com/KingLoip/synpora/actions/runs/38538892591)).
- [ ] Confirm at least one successful post-merge scheduled monitor invocation (15-minute schedule).
- [ ] `/health`, `/api/v1/system/production-readiness`, `/api/v1/market/data-health` and `/ops` return their expected contracts in repeated scheduled runs.
- [ ] `/api/v1/system/collector-status` exposes attempt/success timestamps, quality, failure count and bounded retry timing; degraded data must not advance the last-success timestamp.
- [x] The unauthenticated `POST /api/v1/system/email-connection-test` is rejected (403); verified by the live production smoke test on 2026-10-14. Keep this endpoint protected (see [CI run](https://github.com/KingLoip/synpora/actions/runs/38539016356)).

## Live data quality and learning eligibility

- [ ] Production readiness reports PostgreSQL configured, reachable and schema-ready.
- [ ] Each required field has explicit external provenance, availability, valid finite values, and source-appropriate freshness.
- [ ] Hashprice and GPU data are no older than 15 minutes for learning eligibility.
- [ ] EUR/USD is no older than 96 hours; Austrian spot electricity is no older than 2 hours.
- [ ] The latest eligible snapshot age is at most 900 seconds at the time of the readiness check.
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
