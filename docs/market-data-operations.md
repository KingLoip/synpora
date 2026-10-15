# SYNPORA market-data operations

## GPU rental price source

SYNPORA reads GPU rental prices only. The Vast.ai integration is read-only: it searches bundle offers and extracts per-GPU hourly prices; it must not create, modify, or terminate instances.

Lookup order:
1. Try the documented GET bundle query using the provider's common L40S labels.
2. If the GET query is rejected with HTTP 400 or returns no offers, try the documented filter-object POST form against `/api/v0/bundles/`.
3. Filter returned offers locally for L40S and validate finite, positive hourly prices before using the median.
4. If the provider returns no usable offers, retain the existing freshness/quality gates and report market data as degraded rather than treating fallback/reference values as live.

The POST fallback uses `type: ondemand`, `rentable: true`, and a bounded result limit. The request is cached for five minutes and response size remains capped. HTTP 401/403 errors are not retried as query-schema errors; verify the operator's API key and read permissions.

## Collector cadence and freshness margin

The internal collector retries a successful live cycle every 12 minutes, leaving a 3-minute margin against the 15-minute freshness gate. Provider-specific cache TTLs remain independent; in particular, the anonymous Zarobit feed is still polled no more often than its 15-minute cache/quota window. Degraded collection uses bounded backoff and must never advance the last-success timestamp.

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
