# SYNPORA production acceptance checklist

This checklist is the release gate for the live Railway deployment. A green CI run proves the tested code path and build; it does not by itself prove delivery to a mailbox, long-term feed reliability, persistence after restart, or business-model accuracy.

## Live release status — 2026-10-14

**Production candidate; business acceptance is still incomplete.** The current main commit `daa5ce2dc9ad732062d626c03f204ee0f1e3f6be` was deployed successfully to Railway production at approximately 08:59:50 UTC. The application and PostgreSQL are online with no Railway warning/critical issues and no pending deployment work.

**Latest live production observation — 2026-10-14 14:42 UTC:** [PR monitor run 7](https://github.com/KingLoip/synpora/actions/runs/38582837554) passed against the live Railway service and canonical domain, including the proposed stricter future-timestamp checks and secret/config-path probes. Production readiness reported `ready`, PostgreSQL reachable and schema-ready, and latest eligible snapshot age 193.6 seconds. Field ages were 0.4 seconds for hashprice, 525.5 seconds for GPU L40S pricing, 52,729 seconds for EUR/USD, and 529 seconds for Austrian spot electricity. All four fields were externally sourced. Collector status was `success/live`, last-success age about 0.7 seconds, duration 703 ms, and zero consecutive failures. The release reported `recommendation_only`, `hardware_write=false`, and `autonomous_control=false`. Data health reported 186 eligible snapshots and 314 excluded/invalid snapshots; reference, mixed, incomplete, and unknown-provenance snapshots are excluded from learning/backtesting.

A transient Energy-Charts timeout at 14:36:55 UTC had caused one degraded collection attempt; a later attempt and the 14:42 live monitor passed. This is evidence of recovery, not yet evidence of sustained provider reliability. Zarobit listings have repeatedly been stale in earlier cycles even though the live check now reports a fresh GPU observation. The successful monitor was triggered by the PR, not by the default-branch schedule.

Verified evidence for this release:
- [Production CI and live smoke test](https://github.com/KingLoip/synpora/actions/runs/38539016356) passed, including syntax/unit tests, API and dashboard smoke tests, Docker build, release invariants, PostgreSQL persistence/migration integration, and the live production smoke test.
- [CodeQL](https://github.com/KingLoip/synpora/actions/runs/38539016634) passed.
- [Production monitor PR run](https://github.com/KingLoip/synpora/actions/runs/38538892591) passed against production after adding checks for the Railway health endpoint, the canonical `synpora.com` homepage, and `synpora.com/ops`.
- [Latest PR #18 production monitor run](https://github.com/KingLoip/synpora/actions/runs/38582837554) passed at 14:42 UTC. The check blocked `/.env`, `/.env.production`, `/.git/config`, and `/.git-credentials` with HTTP 404, validated public entrypoints and release safety, and rejected future-dated observations outside tolerance. [Earlier PR #18 CI run](https://github.com/KingLoip/synpora/actions/runs/38577046601) and [CodeQL run](https://github.com/KingLoip/synpora/actions/runs/38577046525) passed; the latest CI and CodeQL run for the future-timestamp change are still in progress. PR #18 is not merged, so the stricter checks and offset schedule are not yet part of the deployed `main` workflow.
- [PR #17 CI run](https://github.com/KingLoip/synpora/actions/runs/38582486577) passed syntax, provider retry unit tests, import/API smoke, JWT regression, dashboard smoke, Docker build, release invariants, and PostgreSQL integration. [CodeQL run](https://github.com/KingLoip/synpora/actions/runs/38582486562) passed. This PR adds bounded provider retries, sanitized HTTP-status diagnostics, and tests for HTTP-date `Retry-After` and oversized serialized responses. PR #17 is not merged or deployed; the live production smoke job is skipped for pull-request events.
- The recurring read-only monitor is present on `main` with `*/15 * * * *`, but no concrete post-merge `schedule` event/run has been found in GitHub Actions. PR #18 proposes minutes 7, 22, 37, and 52 and logs event/ref/SHA. Do not count a pull-request event as proof of scheduled execution.
- The monitor enforces PostgreSQL readiness, external provenance and per-field freshness limits, successful/live collector status, zero consecutive collector failures, and the safety invariants `hardware_write=false` and `autonomous_control=false`.

These checks are point-in-time evidence, not proof of sustained provider reliability, actual email delivery, restart persistence in production, backup recoverability, or business-model accuracy. Do not mark the product fully production-ready until the applicable manual gates below have recorded evidence.

**Next operational action:** continue observing repeated collector cycles and scheduled monitor runs. No secret is needed while the built-in feed supplies fresh external prices. If the GPU field becomes stale, configure a legitimate `SYNPORA_VAST_API_KEY` in Railway Variables or a controlled HTTPS feed with an exact hostname allowlist. Never paste secrets into chat or GitHub. See [GPU market-data recovery runbook](GPU_MARKET_DATA_RECOVERY.md).

## Safety invariants — must always pass

- [x] Release endpoint reports `mode: recommendation_only` (verified by live monitor run 38582837554 on 2026-10-14).
- [x] `hardware_write=false` and `autonomous_control=false` (verified by live monitor run 38582837554 and production smoke test; see [CI run](https://github.com/KingLoip/synpora/actions/runs/38539016356)).
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

- [x] Production readiness reported PostgreSQL configured, reachable and schema-ready in live monitor run 38582837554 (2026-10-14 14:42 UTC).
- [x] Each required field had explicit external provenance, availability, valid finite values, and source-appropriate freshness at that check.
- [x] Hashprice (0.4 seconds) and GPU price (525.5 seconds) were no older than 15 minutes for learning eligibility at that check.
- [x] EUR/USD (52,729 seconds) was younger than 96 hours; Austrian spot electricity (529 seconds) was younger than 2 hours.
- [x] The latest eligible snapshot age was 193.6 seconds at that check.
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
