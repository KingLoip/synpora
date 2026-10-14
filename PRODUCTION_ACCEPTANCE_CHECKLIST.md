# SYNPORA production acceptance checklist

This checklist is the release gate for the live Railway deployment. A green CI run proves the tested code path and build; it does not by itself prove delivery to a mailbox, long-term feed reliability, persistence after restart, or business-model accuracy.

## Live release status — 2026-10-14

**Production candidate; business acceptance is still incomplete.** The current main commit `daa5ce2dc9ad732062d626c03f204ee0f1e3f6be` was deployed successfully to Railway production at approximately 08:59:50 UTC. The application and PostgreSQL were subsequently reported online with no Railway warning/critical issues and no pending deployment work.

**Latest read-only production observation — 2026-10-14 14:00 UTC:** [PR monitor run](https://github.com/KingLoip/synpora/actions/runs/38576697534) passed against the live Railway service and canonical domain. It reported release 1.3.1 in `recommendation_only`, PostgreSQL reachable and schema-ready, readiness `ready`, latest eligible snapshot age 548.6 seconds, collector last-success age 548.4 seconds, and zero consecutive collector failures. Per-field external observation ages were 3.1 seconds for hashprice, 3.1 seconds for the Vast.ai L40S price, 136,294.7 seconds for EUR/USD (within the 96-hour limit), and 394.7 seconds for Austrian spot electricity (within the 2-hour limit). The release endpoint explicitly returned `hardware_write=false` and `autonomous_control=false`.

The same live observation is point-in-time evidence only: a collector attempt at 13:51:40 UTC still logged that all six Zarobit L40S listings were stale, while the Vast.ai fallback supplied the fresh GPU value. Repeated successful cycles are still required to establish sustained provider reliability.

Verified evidence for this release:
- [Production CI and live smoke test](https://github.com/KingLoip/synpora/actions/runs/38539016356) passed, including syntax/unit tests, API and dashboard smoke tests, Docker build, release invariants, PostgreSQL persistence/migration integration, and the live production smoke test.
- [CodeQL](https://github.com/KingLoip/synpora/actions/runs/38539016634) passed.
- [Production monitor PR run](https://github.com/KingLoip/synpora/actions/runs/38538892591) passed against production after adding checks for the Railway health endpoint, the canonical `synpora.com` homepage, and `synpora.com/ops`.
- [Proposed secret/config-path probes](https://github.com/KingLoip/synpora/actions/runs/38553898132) also passed against production: `/.env`, `/.env.production`, `/.git/config`, and `/.git-credentials` all returned HTTP 404. These extra probes are in [PR #16](https://github.com/KingLoip/synpora/pull/16) and are not yet part of the scheduled `main` workflow.
- The recurring read-only monitor is present on `main` with `*/15 * * * *`, but no concrete post-merge `schedule` event/run has been found in GitHub Actions. [PR #18](https://github.com/KingLoip/synpora/pull/18) proposes offsetting the schedule to minutes 7, 22, 37, and 52 and logging event/ref/SHA; its live monitor check, CI and CodeQL passed, but it is not merged. Do not count a pull-request event as proof of scheduled execution.
- The monitor enforces PostgreSQL readiness, external provenance and per-field freshness limits, successful/live collector status, zero consecutive collector failures, and the safety invariants `hardware_write=false` and `autonomous_control=false`.

These checks are point-in-time evidence, not proof of sustained provider reliability, actual email delivery, restart persistence in production, backup recoverability, or business-model accuracy. Do not mark the product fully production-ready until the applicable manual gates below have recorded evidence.

**Next operational action:** continue observing scheduled monitor runs and repeated collector cycles. No secret is needed while the built-in feed supplies fresh external prices. If the GPU field becomes stale, configure a legitimate `SYNPORA_VAST_API_KEY` in Railway Variables or a controlled HTTPS feed with an exact hostname allowlist. Never paste secrets into chat or GitHub. See [GPU market-data recovery runbook](GPU_MARKET_DATA_RECOVERY.md).

## Safety invariants — must always pass

- [x] Release endpoint reports `mode: recommendation_only` (verified by live monitor run 38576697534 on 2026-10-14).
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

- [x] Production readiness reported PostgreSQL configured, reachable and schema-ready in live monitor run 38576697534 (2026-10-14 14:00 UTC).
- [x] Each required field had explicit external provenance, availability, valid finite values, and source-appropriate freshness at that check.
- [x] Hashprice (3.1 seconds) and GPU price (3.1 seconds) were no older than 15 minutes for learning eligibility at that check.
- [x] EUR/USD (136,294.7 seconds) was younger than 96 hours; Austrian spot electricity (394.7 seconds) was younger than 2 hours.
- [x] The latest eligible snapshot age was 548.6 seconds at that check.
- [ ] Reference/fallback values remain clearly distinguished and are excluded from learning/backtesting.
- [ ] Review the collector status for repeated failures and confirm snapshots continue to arrive over time. A single fresh snapshot is not evidence of durable feed reliability.
- [ ] If GPU rental prices need to be live rather than reference estimates, configure a provider API key or an HTTPS custom feed with an exact host allowlist; do not weaken freshness limits to make readiness pass.

## Email — configuration is not delivery proof

- [x] Production readiness reported SMTP configured and email verification required in the successful live production smoke test (CI run 38539016356).
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
