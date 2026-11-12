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

The production forecast gate is evaluated independently at 1h, 6h, 24h and 72h. Each horizon requires at least 100 genuine, non-overlapping out-of-sample pairs, at least 5% MAE skill over persistence, a positive lower 95% bootstrap confidence bound, and eligible BTC and GPU strategy selectors. A horizon that lacks enough independent history remains blocked; elapsed calendar time alone never grants eligibility.

The 2026-11-12 validation run (workflow run 43298516389) observed 12 genuine 1h pairs, 1 six-hour pair, 0 twenty-four-hour pairs and 0 seventy-two-hour pairs. The validator estimated about 77.92 additional days for the 24h horizon and 287.68 days for the 72h horizon to reach the 100-pair data-volume lower bound at the observed cadence. These are planning estimates only: model skill, confidence bounds and selector quality may still fail after the sample grows.

Operational requirements:

1. Keep the production collector running at its configured cadence; do not backfill repeated cached quotes as new observations.
2. Preserve provider observation timestamps, collection timestamps, data-quality provenance and exclusions so each candidate pair can be audited.
3. Track per-horizon genuine pair counts, persistence fallbacks, selector fallbacks, MAE skill and bootstrap lower confidence bounds.
4. Treat any missing/insufficient horizon as blocked. Do not weaken the 100-pair, 5% skill, positive confidence-bound or per-strategy requirements to obtain a green release.
5. Keep forecast technical validation separate from investment validation. Market asking prices are not realized GPU payouts or BTC pool settlements; modeled energy prices are not all-in site invoices; battery estimates are not metered battery outcomes.
6. Keep recommendation-only safety invariants in force: `hardware_write=false` and `autonomous_control=false`.

Actual investment validation additionally requires site-specific electricity invoices and export settlements, GPU payout statements, BTC pool settlement records, hardware/capex invoices, maintenance/overhead records and metered battery/degradation evidence.
