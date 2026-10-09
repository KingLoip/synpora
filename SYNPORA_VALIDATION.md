# SYNPORA validation and paper-trading guide

## What is implemented

- `GET /api/v1/market/data-health` reports stored snapshot counts, latest source/provenance, age of the latest eligible observation, and why data is excluded.
- `POST /api/v1/farms/{farm_id}/backtest` performs strict walk-forward evaluation. At time t it chooses the highest modeled value using snapshot t, then scores that choice against the next later eligible snapshot. It never trains or scores on a snapshot that was not fully externally sourced.
- `GET /api/v1/farms/{farm_id}/paper-trading` summarizes recommendation-ledger outcomes, prediction error, regret, open decisions and per-strategy results. It does not place orders or control equipment.
- Existing forecast fitting, model weights and learning settlement use the same provenance gate.

All farm endpoints require a valid bearer token and ownership of the farm. The market data health endpoint contains only service-level market-data diagnostics.

## Eligibility requirements

A snapshot is eligible only when all four fields below have an explicit `data_quality.fields.<field>.source = "external"` and `available = true`:

- `btc_hashprice_usd_ph_day`
- `gpu_l40s_usd_hour`
- `eur_usd`
- `austria_spot_eur_kwh`

A mixed snapshot is not partially used for training/backtesting. Reference-only and legacy snapshots remain visible for diagnosis but are excluded from model fitting, walk-forward backtesting and learning settlement.

## Suggested validation sequence

1. Check `GET /api/v1/market/data-health`. Do not expect a backtest to be considered evaluated until enough eligible consecutive observations exist.
2. Call `POST /api/v1/farms/{farm_id}/backtest` with `{"min_samples":24,"max_snapshots":1000}`.
3. Review `metrics.prediction_mae_eur_kwh`, `metrics.mean_regret_eur_kwh`, `metrics.strategy_selection_accuracy`, and `benchmark_by_strategy`.
4. Check `GET /api/v1/farms/{farm_id}/paper-trading` regularly. At least 24 settled recommendations are required before its status becomes `evaluation_available`; this is a minimum reporting threshold, not proof of statistical reliability.
5. Use a longer holdout period before changing model weights or considering any operational automation.

## Important limitations

- The current Startmining collector can provide selected Bitcoin market fields, but EUR/USD, Austrian spot energy and GPU pricing remain reference values unless a verified external provider supplies them. A response marked `partial` or `fallback` is not an eligible learning label.
- Operators can configure `SYNPORA_MARKET_DATA_URL` to an HTTPS JSON endpoint. The feed must provide a recent Unix `timestamp` (seconds or milliseconds) and verified values for the required fields. Data older than 15 minutes is rejected. Provider configuration alone does not prove that the endpoint returns trustworthy data; monitor `data-health` and field-level provenance.
- Risk analysis now includes P05, a lower-tail expected-shortfall metric, probability of negative outcomes, and four deterministic stress cases. `risk_gate` is a warning based on simulated assumptions, not a safety guarantee.
- A successful API response or CI test does not prove that a live provider is available, that Railway secrets are set, or that production PostgreSQL persists data after restart.
- Backtesting measures historical simulated strategy values using the model's assumptions. It is not a promise of future returns and is not a substitute for fees, tax, hardware depreciation, downtime, contract availability or full site-specific engineering inputs.
- Keep `hardware_write=false` and `autonomous_control=false`. These APIs are recommendation and observation tools only.
