# SYNPORA GPU market-data recovery runbook

## Current production incident (2026-10-13)

The earlier failure mode was `status: configuration_required` and `market_data_ready: false`: the GPU field was a reference fallback, and the latest eligible snapshot exceeded 900 seconds. The collector counted six timestamped Zarobit L40S offers but zero fresh offers, and one attempt reported no fresh Austrian Energy-Charts observation.

**Latest observed recovery (2026-10-13 around 16:40 UTC):** live readiness and CI smoke passed. The GPU field was external (`Zarobit L40S on-demand listing median`, age 318.4 seconds), and the latest eligible snapshot age was 298.3 seconds. CodeQL passed too. This was a transient point-in-time success; it does not establish sustained provider reliability. Continue to inspect collector status and confirm repeated fresh cycles. If freshness regresses, leave release readiness blocked and use the recovery steps below.

## Current failure mode

The production readiness gate requires a recent, externally sourced L40S hourly price. A reference fallback is useful for demonstration only; it must not make the collector, learning pipeline, or production readiness appear live.

When the public Zarobit feed has no fresh usable offers, the collector records a degraded attempt and retries with bounded backoff. The production smoke test should remain red until a valid external GPU price is available.

## Preferred recovery: Vast.ai API key

1. Obtain an API key directly from Vast.ai using the account that is authorized to access its marketplace API.
2. In Railway, open the **synpora** service in the **production** environment and open **Variables**.
3. Add `SYNPORA_VAST_API_KEY` as a Railway variable. Paste the key only into Railway's secret/value field; never put it in GitHub, an issue, a log, or ChatGPT.
4. Allow Railway to redeploy/restart the service if required for the variable to take effect.
5. Check `https://synpora.com/api/v1/system/production-readiness` and `https://synpora.com/api/v1/market/data-health`.
6. Confirm the GPU field reports `source: external`, a named provider, a finite positive value, and an age no greater than 900 seconds. Confirm the latest fully eligible snapshot is no older than 900 seconds and readiness reports `ready`.
7. Check `/api/v1/system/collector-status` and Railway runtime logs for a successful collection. A successful HTTP response alone is not sufficient; `data_quality.status` must be `live`.

Do not share the key with anyone and do not send it through chat. I cannot verify the key's value, account permissions, or marketplace access without a secret-safe provider-side check.

## Alternative: operator-hosted HTTPS feed

If using `SYNPORA_MARKET_DATA_FEED_URL`, configure the exact hostname in `SYNPORA_MARKET_DATA_ALLOWED_HOSTS`. Use HTTPS, a valid certificate, a trusted host, and a payload with a fresh observation timestamp and the required fields. The allowlist must contain the hostname only, not a URL path. Do not allow arbitrary hosts or private/internal destinations.

The payload must include external values for:
- `btc_hashprice_usd_ph_day`
- `gpu_l40s_usd_hour`
- `eur_usd`
- `austria_spot_eur_kwh`

The GPU and hashprice observations must be no older than 900 seconds; EUR/USD no older than 96 hours; Austrian spot electricity no older than 7200 seconds. Every required value must be finite and within its validation range. A reference estimate or missing timestamp is not live data.

## Vast.ai HTTP authentication diagnostics

The collector logs the Vast.ai HTTP status code without logging the Authorization header or API key. Interpret it as follows:

- **401**: the key was rejected or is malformed/revoked. Create a replacement only if the key cannot be verified, then update the Railway secret and redeploy.
- **403**: the key may be valid but lacks permission for marketplace offer reads, or the account/team policy blocks the endpoint. Review the key's read permissions for offer/instance search; do not enable write permissions just to read prices.
- **429**: the provider is rate-limiting requests. Keep the bounded retry/backoff and avoid manual repeated collection.
- **5xx**: provider-side failure; retain the public provider fallback and retry later.

Never log the response headers, request Authorization value, full exception text, or API-key query parameter. A successful HTTP response still does not count as recovery unless fresh L40S prices are parsed and the full market-data readiness gate passes.

## Triage order

1. Inspect `/api/v1/system/production-readiness` for `market_data_field_diagnostics`, `market_data_guidance`, and `market_data_latest_eligible_age_seconds`.
2. Inspect `/api/v1/system/collector-status` for the last attempt, last success, and consecutive failures.
3. Compare the provider observation timestamp with current UTC time; do not trust fetch time alone as observation time.
4. If the GPU provider has zero fresh offers, configure the Vast.ai key or a controlled HTTPS feed. If the Austrian spot feed is stale, check the provider's availability and timestamp semantics separately.
5. Wait for a complete, externally sourced snapshot with every required field fresh; then rerun CI/live smoke and observe repeated successful collector cycles before release.

## Acceptance / rollback rule

- Keep `hardware_write=false`, `autonomous_control=false`, and recommendation-only mode.
- Do not lower freshness thresholds or relabel a reference price as external to pass readiness.
- If the provider API or payload fails validation, remove/repair the invalid configuration and leave readiness blocked.
- After recovery, require successful CI, CodeQL, live smoke, and a sustained window of healthy collector observations before considering a full production release.
