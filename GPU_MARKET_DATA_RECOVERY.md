# SYNPORA GPU market-data recovery runbook

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

## Acceptance / rollback rule

- Keep `hardware_write=false`, `autonomous_control=false`, and recommendation-only mode.
- Do not lower freshness thresholds or relabel a reference price as external to pass readiness.
- If the provider API or payload fails validation, remove/repair the invalid configuration and leave readiness blocked.
- After recovery, require successful CI, CodeQL, live smoke, and a sustained window of healthy collector observations before considering a full production release.
