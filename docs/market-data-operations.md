# SYNPORA market-data operations

## International market data and site tariffs

Global market readiness uses only fresh, externally sourced Bitcoin hashprice, L40S rental pricing, and EUR/USD. Regional electricity feeds are not fetched as a universal price: the legacy Austria spot field is reference-only for backwards compatibility and must never be used as a farm's site tariff. The collector and data-health endpoint distinguish globally eligible market snapshots from snapshots that also contain a verified site tariff.

International walk-forward backtests can use an explicitly supplied constant `energy_cost_eur_kwh` (range -1.0 to 10.0 EUR/kWh). The result is assumption-based and reports `energy_cost_mode=user_supplied_constant`; it is not evidence that the tariff matches a real site. Site-specific online learning must use a tariff supplied with a decision or a verified site-tariff observation. Never invent a tariff.

## GPU rental price source

SYNPORA reads GPU rental prices only. The Vast.ai integration is read-only: it searches bundle offers and extracts per-GPU hourly prices; it must not create, modify, or terminate instances.

Lookup order:
1. Try the documented GET bundle query using the provider's common L40S labels.
2. If the GET query is rejected with HTTP 400 or returns no offers, try the documented filter-object POST form against `/api/v0/bundles/`.
3. Filter returned offers locally for L40S and validate finite, positive hourly prices before using the median.
4. If the provider returns no usable offers, retain the existing freshness/quality gates and report market data as degraded rather than treating fallback/reference values as live.

The POST fallback uses `type: ondemand`, `rentable: true`, and a bounded result limit. The request is cached for five minutes and response size remains capped. HTTP 401/403 errors are not retried as query-schema errors; verify the operator's API key and read permissions.

## Collector cadence and freshness margin

The internal collector retries a successful live cycle every 12 minutes, leaving a 3-minute margin against the 15-minute freshness gate. Provider-specific cache TTLs remain independent; in particular, the anonymous Zarobit feed is still polled no more often than its 15-minute cache/quota window. If Zarobit's listing timestamps are stale, the collector falls back to Lium's public rentable-node feed, which carries a generated timestamp and is rebuilt at most once per minute. Only current, available L40S offers with finite positive per-GPU prices count; missing/stale timestamps, reference prices, and unavailable nodes are rejected. Degraded collection uses bounded backoff and must never advance the last-success timestamp.

## Production checks

- `/health`: process liveness only; not proof of market-data quality.
- `/api/v1/market/data-health`: market snapshot freshness and field provenance.
- `/api/v1/system/production-readiness`: deployment configuration/readiness gates.
- `/api/v1/system/collector-status`: collection attempts, last successful live snapshot, retry/backoff state.
- `/ops`: read-only operational dashboard.

Never interpret a successful HTTP 200 from collection as proof that the data is live. Only a fresh snapshot with the required external fields passes the market-quality gate.

## Safety invariants

- `hardware_write=false`
- `autonomous_control=false`
- Recommendation-only operation
- No GPU instances are created as part of market-data collection.

## Forecast evidence accumulation and release gates

The economic validator uses timestamped, externally sourced market snapshots. Collection time and provider observation time are separate: repeated reads of a cached quote, a stale provider response, or a persistence fallback must not be counted as a new genuine model observation.

The production forecast gate is evaluated independently at 1h, 6h, 24h and 72h. Each horizon requires all of:
- At least 100 genuine, non-overlapping out-of-sample BTC+GPU pairs.
- At least 5% MAE skill over persistence.
- A positive lower 95% block-bootstrap confidence bound.
- At least 32 horizon-separated training origins (24 fit and 8 holdout), with no more than 8 overlapping origins per horizon.
- No BTC or GPU persistence fallback counted as a genuine model pair.

A horizon that lacks enough independent history remains blocked; elapsed calendar time alone never grants eligibility.

### Latest strict validation snapshot

Workflow: **SYNPORA Economic Validation #43761952828**, commit `9568c1f03497dc349e1f7132c45d910a4767d0dc`, completed 2026-11-15 21:09 UTC. SYNPORA CI, browser smoke and isolated PostgreSQL recovery passed. The strict economic job exited non-zero because the empirical forecast gate remained blocked; its diagnostic artifact uploaded successfully.

Production history contained 3,858 usable external market observations from 2026-10-10 through 2026-11-15, with a 12.03-minute median cadence. The new Mempool historical network-hashrate backfill succeeded: 365 daily points received and 2,838 market observations backfilled. Alignment uses only the latest completed daily bucket at or before each market observation, with a maximum age of 36 hours and no look-ahead. This resolves the missing historical hash-rate input for this validator run, but it does not create additional GPU price observations or prove settled mining revenue.

International tariff scenarios share the same market-price forecast diagnostics. The moderate reference scenario reported:
| Horizon | Joint genuine BTC+GPU OOS pairs | Joint MAE skill | BTC independent pairs / skill | GPU independent pairs / skill | Persistence selector fallbacks (BTC / GPU) | Status |
|---|---:|---:|---:|---:|---:|---|
| 1h | 11 | +46.360% | 42 / -0.042% | 157 / +21.041% | 643 / 528 | Blocked |
| 6h | 0 | Not available | 3 / +12.747% | 38 / -108.252% | 106 / 71 | Blocked |
| 24h | 0 | Not available | 1 / -0.521% | 9 / -128.217% | 23 / 15 | Blocked |
| 72h | 0 | Not available | 0 / Not available | 2 / +51.802% | 4 / 2 | Blocked |

The 1h GPU-only diagnostic has 157 pairs and a positive lower 95% bootstrap bound (+9.563%), but BTC-only evidence has 42 pairs and slightly negative skill. The combined BTC+GPU gate has only 11 genuine pairs, below the 100-pair minimum, with 643 BTC and 528 GPU persistence-selector fallbacks; its confidence bound is unavailable. Therefore the aggregate 1h skill is not release evidence. All longer joint horizons have zero genuine paired samples. Per-series diagnostics never replace the joint BTC+GPU gate.

The backfill improved BTC data provenance but did **not** make the BTC model forecast independently useful: 1h BTC independent skill remains slightly negative, and the combined sample remains very small. Next work should focus on the source-quality and model-selection causes of persistence fallbacks, while preserving timestamp provenance and all current gates. Do not synthesize observations, count persistence fallbacks as genuine pairs, or lower the 100-pair / 5% skill / positive-confidence-bound criteria.

### Historical BTC network-hashrate source

The validator has a branch-only, read-only backfill from Mempool's public `/api/v1/mining/hashrate/1y` endpoint. It normalizes `hashrates[].avgHashrate` from H/s to EH/s, deduplicates timestamps, and aligns each completed daily bucket only to observations at or after its timestamp, with a maximum age of 36 hours. Future buckets are never used. Existing fresh live hashrate values take precedence. If the endpoint is unavailable or malformed, the validator falls back to its existing provenance rules and does not relax any release gate.

Workflow #43761952828 confirms that the endpoint returned 365 points and that 2,838 historical observations were backfilled. This is verified for the PR branch's CI validator only; it does not alter the production collector, production database or deployed service. The Mempool REST documentation lists the network-wide hashrate endpoint and supported time periods including 1y: https://mempool.space/docs/api/rest

### Operational requirements

1. Keep the production collector running at its configured cadence; do not backfill repeated cached quotes as new observations.
2. Preserve provider observation timestamps, collection timestamps, data-quality provenance and exclusions so each candidate pair can be audited.
3. Track per-horizon genuine pair counts, persistence fallbacks, selector fallbacks, MAE skill and bootstrap lower confidence bounds.
4. Treat any missing/insufficient horizon as blocked. Do not weaken the 100-pair, 5% skill, positive confidence-bound, training-origin or per-strategy requirements to obtain a green release.
5. Keep forecast technical validation separate from investment validation. Market asking prices are not realized GPU payouts or BTC pool settlements; modeled energy prices are not all-in site invoices; battery estimates are not metered battery outcomes.
6. Keep recommendation-only safety invariants in force: `hardware_write=false` and `autonomous_control=false`.

Actual investment validation additionally requires site-specific electricity invoices and export settlements, GPU payout statements, BTC pool settlement records, hardware/capex invoices, maintenance/overhead records and metered battery/degradation evidence.

## Sensitive-path reconnaissance monitoring

The ASGI request middleware classifies common secret/configuration discovery paths (for example `.env` variants and backups, `.git/config`, `.aws`, `.ssh`, `.claude`, `.cargo`, `.gem`, `.dbeaver`, credential files, PHP diagnostics and server-status probes). It emits a warning when one direct peer reaches the configured default threshold of 10 classified probes in a rolling five-minute window, or immediately if a classified path unexpectedly returns HTTP 2xx/3xx. Expected isolated 404 responses remain quiet. A threshold crossing emits one burst warning rather than one warning per subsequent request.

The monitor is process-local and bounded to 2,048 source fingerprints. It retains only a short keyed hash of the direct ASGI peer, a low-cardinality path category and monotonic timestamps; raw paths and IP addresses are not written to these alerts. Counters reset on process restart and are not a substitute for a durable WAF or edge-level security log. Forwarded client-IP headers are deliberately ignored: do not enable proxy-header trust until the exact trusted Railway proxy chain is verified. This monitoring is alert-only; it does not block requests, change firewall rules, rotate secrets or alter production infrastructure. It is branch-only until the associated pull request is deliberately reviewed, merged and deployed.

Tests cover encoded-path normalization, false positives, expected 404 noise suppression, threshold crossing, unexpected successful responses, expiry, bounded source cardinality and source fingerprint privacy.
