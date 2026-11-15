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

Workflow: **SYNPORA Economic Validation #304**, commit `dc102aa6a883d2c38a9b10a27c0dc0dc63fed1e7`, completed 2026-11-14 21:02 UTC. The validation job exited non-zero because the strict empirical release gate correctly remained blocked; diagnostic artifact upload succeeded.

International stress-reference joint BTC+GPU results:
| Horizon | Genuine OOS pairs | MAE skill | Persistence/selector fallbacks | Additional observation lower bound | Status |
|---|---:|---:|---:|---:|---|
| 1h | 12 | +45.431% | 676 | Not sufficient to release | Blocked |
| 6h | 1 | -0.039% | 115 | Not sufficient to release | Blocked |
| 24h | 0 | Not available | 30 | 9,095 (~76.01 days) | Blocked |
| 72h | 0 | Not available | 10 | 34,194 (~285.77 days) | Blocked |

The observation/day figures are only a data-volume lower bound at the observed cadence. They do not guarantee the 100 genuine pairs, model skill, selector eligibility or positive confidence bound. The 1h aggregate skill is not release evidence because the sample remains below 100, selector/persistence fallbacks are present, and the bootstrap lower bound is not proven positive.

Independent 1h per-series diagnostics are informative but do not replace the joint gate: BTC had 44 pairs, -0.462% skill and a -25.007% lower 95% CI; GPU had 151 pairs, +20.684% skill and a +8.795% lower 95% CI. The BTC series therefore remains a material weakness even though GPU diagnostics are positive.

### Operational requirements

1. Keep the production collector running at its configured cadence; do not backfill repeated cached quotes as new observations.
2. Preserve provider observation timestamps, collection timestamps, data-quality provenance and exclusions so each candidate pair can be audited.
3. Track per-horizon genuine pair counts, persistence fallbacks, selector fallbacks, MAE skill and bootstrap lower confidence bounds.
4. Treat any missing/insufficient horizon as blocked. Do not weaken the 100-pair, 5% skill, positive confidence-bound, training-origin or per-strategy requirements to obtain a green release.
5. Keep forecast technical validation separate from investment validation. Market asking prices are not realized GPU payouts or BTC pool settlements; modeled energy prices are not all-in site invoices; battery estimates are not metered battery outcomes.
6. Keep recommendation-only safety invariants in force: `hardware_write=false` and `autonomous_control=false`.

Actual investment validation additionally requires site-specific electricity invoices and export settlements, GPU payout statements, BTC pool settlement records, hardware/capex invoices, maintenance/overhead records and metered battery/degradation evidence.

## Sensitive-path reconnaissance monitoring

The ASGI request middleware classifies common secret/configuration discovery paths (for example `.env`, `.git/config`, `.aws/credentials`, `.ssh`, PHP diagnostics and server-status probes). It emits a warning when one direct peer reaches the configured default threshold of 10 classified probes in a rolling five-minute window, or immediately if a classified path unexpectedly returns HTTP 2xx/3xx. Expected isolated 404 responses remain quiet. A threshold crossing emits one burst warning rather than one warning per subsequent request.

The monitor is process-local and bounded to 2,048 source fingerprints. It retains only a short keyed hash of the direct ASGI peer, a low-cardinality path category and monotonic timestamps; raw paths and IP addresses are not written to these alerts. Counters reset on process restart and are not a substitute for a durable WAF or edge-level security log. Forwarded client-IP headers are deliberately ignored: do not enable proxy-header trust until the exact trusted Railway proxy chain is verified. This monitoring is alert-only; it does not block requests, change firewall rules, rotate secrets or alter production infrastructure. It is branch-only until the associated pull request is deliberately reviewed, merged and deployed.

Tests cover encoded-path normalization, false positives, expected 404 noise suppression, threshold crossing, unexpected successful responses, expiry, bounded source cardinality and source fingerprint privacy.
