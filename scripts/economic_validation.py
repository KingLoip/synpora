#!/usr/bin/env python3
"""Read-only, empirical SYNPORA economic validation using production market history.

This is an independent validation harness, not a production trading component.
It deliberately labels market-to-market results as modeled economics, not realized cashflow.
"""
from __future__ import annotations

import json
import math
import random
import statistics
import sys
import urllib.request
from datetime import datetime, timezone

MARKET_URL = "https://synpora-production.up.railway.app/api/v1/market/history?limit=500"
TARIFFS = {
    # Salzburg AG Strom Privat 24 2026 product sheet: all-in variable price,
    # incl. network/taxes, less the 8.28 ct/kWh gross energy-price discount
    # for the first 1,500 kWh. Metering charges and fixed annual fees excluded.
    "salzburg_2026_first_1500_discounted": 0.216551,
    "salzburg_2026_standard_after_1500": 0.299351,
    # Transparent international sensitivity point, not the user's tariff.
    "low_cost_international_reference": 0.05,
}
WINDOW = 24
BOOTSTRAP_SAMPLES = 1000
RISK_AVERSION = 0.75
SEED = 20261030


def finite(value):
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError, OverflowError):
        return None


def market_ready(payload):
    if not isinstance(payload, dict):
        return False
    fields = (payload.get("data_quality") or {}).get("fields", {})
    requirements = {
        "btc_hashprice_usd_ph_day": 900,
        "gpu_l40s_usd_hour": 900,
        "eur_usd": 96 * 3600,
    }
    for key, max_age in requirements.items():
        field = fields.get(key)
        value = finite(payload.get(key))
        age = finite(field.get("age_seconds")) if isinstance(field, dict) else None
        if (not isinstance(field, dict) or field.get("source") != "external"
                or field.get("available") is not True or value is None or value <= 0
                or age is None or age < -60 or age > max_age):
            return False
    return True


def observations_from_history(raw):
    result = []
    rejected = 0
    for item in raw if isinstance(raw, list) else []:
        payload = item.get("data") if isinstance(item, dict) else None
        ts = finite(item.get("timestamp")) if isinstance(item, dict) else None
        if ts is None or not market_ready(payload):
            rejected += 1
            continue
        def optional(key, default, lower=0.000001, upper=None):
            value = finite(payload.get(key))
            if value is None or value < lower or (upper is not None and value > upper):
                return default
            return value
        result.append({
            "ts": ts,
            "btc": float(payload["btc_hashprice_usd_ph_day"]),
            "gpu": float(payload["gpu_l40s_usd_hour"]),
            "fx": float(payload["eur_usd"]),
            "gpu_power": optional("gpu_l40s_power_kw", 0.35),
            "gpu_util": optional("gpu_utilization", 0.70, 0.0, 1.0),
            "gpu_fee": optional("gpu_platform_fee", 0.15, 0.0, 1.0),
        })
    result.sort(key=lambda row: row["ts"])
    deduped = []
    for row in result:
        if deduped and row["ts"] <= deduped[-1]["ts"]:
            rejected += 1
            continue
        deduped.append(row)
    return deduped, rejected


def economics(row, tariff, gpu_capex=0.0, btc_capex=0.0):
    """Mirror saas_api.py _economics defaults and units (EUR per site kWh)."""
    btc_gross = (row["btc"] / row["fx"]) / (20.0 * 24.0) * 0.98
    gpu_gross = ((row["gpu"] / row["fx"]) * row["gpu_util"]
                 * (1.0 - row["gpu_fee"])) / row["gpu_power"]
    btc_fixed = (btc_capex / 4.0) / (3.5 * 8760.0)
    gpu_fixed = (gpu_capex / 5.0) / (row["gpu_power"] * 8760.0)
    btc = 0.98 * (btc_gross - tariff) - btc_fixed
    gpu = 0.98 * (gpu_gross - tariff) - gpu_fixed
    battery = max(0.0, 0.071 * 0.90 - 0.015)
    grid = max(0.0, 0.055)
    return {"AI Compute": gpu, "BTC Mining": btc, "Battery": battery, "Grid": grid}


def quantile(values, q):
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, int((len(ordered) - 1) * q)))]


def distribution(values):
    if not values:
        return None
    tail = sorted(values)[:max(1, math.ceil(len(values) * 0.05))]
    return {
        "mean_eur_per_kwh": round(statistics.fmean(values), 6),
        "median_eur_per_kwh": round(statistics.median(values), 6),
        "p05_eur_per_kwh": round(quantile(values, 0.05), 6),
        "p10_eur_per_kwh": round(quantile(values, 0.10), 6),
        "expected_shortfall_p05_eur_per_kwh": round(statistics.fmean(tail), 6),
        "probability_positive": round(sum(v > 0 for v in values) / len(values), 4),
        "probability_negative": round(sum(v < 0 for v in values) / len(values), 4),
        "volatility_eur_per_kwh": round(statistics.pstdev(values), 6),
    }


def empirical_risk_choice(history, current, tariff, seed):
    """Paired bootstrap of prior BTC/GPU/FX returns; no future observations leak in."""
    returns = []
    start = max(1, len(history) - WINDOW)
    for j in range(start, len(history)):
        prev, cur = history[j - 1], history[j]
        if prev["btc"] <= 0 or prev["gpu"] <= 0 or prev["fx"] <= 0:
            continue
        returns.append((math.log(cur["btc"] / prev["btc"]),
                        math.log(cur["gpu"] / prev["gpu"]),
                        math.log(cur["fx"] / prev["fx"])))
    if len(returns) < 8:
        return None
    rng = random.Random(seed)
    samples = {name: [] for name in ("AI Compute", "BTC Mining", "Battery", "Grid")}
    wins = {name: 0 for name in samples}
    for _ in range(BOOTSTRAP_SAMPLES):
        rb, rg, rf = rng.choice(returns)
        future = {
            **current,
            "btc": current["btc"] * math.exp(rb),
            "gpu": current["gpu"] * math.exp(rg),
            "fx": current["fx"] * math.exp(rf),
        }
        values = economics(future, tariff)
        winner = max(values, key=values.get)
        wins[winner] += 1
        for name, value in values.items():
            samples[name].append(value)
    metrics = {}
    for name, values in samples.items():
        mean = statistics.fmean(values)
        p05 = quantile(values, 0.05)
        metrics[name] = {
            "mean": mean,
            "p05": p05,
            "score": (1.0 - RISK_AVERSION) * mean + RISK_AVERSION * p05,
            "win_probability": wins[name] / BOOTSTRAP_SAMPLES,
            "probability_positive": sum(v > 0 for v in values) / BOOTSTRAP_SAMPLES,
        }
    chosen = max(metrics, key=lambda name: metrics[name]["score"])
    return chosen, metrics


def run_backtest(rows, tariff, min_history=WINDOW):
    pairs = []
    # At t, train only on returns ending at t; score against t+1.
    for i in range(min_history, len(rows) - 1):
        decision = rows[i]
        next_row = rows[i + 1]
        if (next_row["ts"] - decision["ts"]) <= 0:
            continue
        risk = empirical_risk_choice(rows[:i + 1], decision, tariff, SEED + i)
        if risk is None:
            continue
        chosen, risk_metrics = risk
        actual = economics(next_row, tariff)
        best = max(actual, key=actual.get)
        pairs.append({
            "chosen": chosen,
            "predicted": risk_metrics[chosen]["mean"],
            "actual": actual[chosen],
            "best": best,
            "regret": max(0.0, actual[best] - actual[chosen]),
            "actual_values": actual,
            "gap_minutes": (next_row["ts"] - decision["ts"]) / 60.0,
        })
    if not pairs:
        return {"status": "insufficient_data", "pairs": 0}
    regrets = [p["regret"] for p in pairs]
    errors = [abs(p["actual"] - p["predicted"]) for p in pairs]
    strategy_means = {
        name: round(statistics.fmean(p["actual_values"][name] for p in pairs), 6)
        for name in ("AI Compute", "BTC Mining", "Battery", "Grid")
    }
    return {
        "status": "evaluated" if len(pairs) >= 24 else "insufficient_data",
        "pairs": len(pairs),
        "mean_prediction_error_eur_per_kwh": round(statistics.fmean(errors), 6),
        "mean_regret_eur_per_kwh": round(statistics.fmean(regrets), 6),
        "p90_regret_eur_per_kwh": round(quantile(regrets, 0.90), 6),
        "strategy_selection_accuracy": round(sum(p["chosen"] == p["best"] for p in pairs) / len(pairs), 4),
        "mean_next_snapshot_value_eur_per_kwh": strategy_means,
        "median_snapshot_gap_minutes": round(statistics.median(p["gap_minutes"] for p in pairs), 2),
        "p90_snapshot_gap_minutes": round(quantile([p["gap_minutes"] for p in pairs], 0.90), 2),
        "warnings": [
            "This is an out-of-sample next-snapshot market mark-to-model test, not realized cashflow.",
            "The paired bootstrap preserves observed BTC/GPU/FX co-movement; it does not prove long-horizon stationarity.",
            "Battery and grid values remain explicit model defaults (7.1 ct/kWh battery reference, 5.5 ct/kWh export reference).",
            "Equipment capex, maintenance, taxes, metering, connection limits and actual uptime are not supplied by the production market feed.",
        ],
    }


def capex_hurdles(latest, tariff):
    btc_gross = (latest["btc"] / latest["fx"]) / (20.0 * 24.0) * 0.98
    gpu_gross = ((latest["gpu"] / latest["fx"]) * latest["gpu_util"]
                 * (1.0 - latest["gpu_fee"])) / latest["gpu_power"]
    btc_margin = 0.98 * (btc_gross - tariff)
    gpu_margin = 0.98 * (gpu_gross - tariff)
    return {
        "gpu_capex_break_even_eur_at_5y_no_maintenance": round(
            max(0.0, gpu_margin) * latest["gpu_power"] * 8760 * 5, 2),
        "btc_capex_break_even_eur_at_4y_no_maintenance": round(
            max(0.0, btc_margin) * 3.5 * 8760 * 4, 2),
    }


def capex_hurdle_distribution(rows, tariff):
    values = [capex_hurdles(row, tariff) for row in rows]
    gpu = [v["gpu_capex_break_even_eur_at_5y_no_maintenance"] for v in values]
    btc = [v["btc_capex_break_even_eur_at_4y_no_maintenance"] for v in values]
    return {
        "gpu_5y_no_maintenance_eur": {
            "median": round(statistics.median(gpu), 2),
            "p05": round(quantile(gpu, 0.05), 2),
            "p90": round(quantile(gpu, 0.90), 2),
        },
        "btc_4y_no_maintenance_eur": {
            "median": round(statistics.median(btc), 2),
            "p05": round(quantile(btc, 0.05), 2),
            "p90": round(quantile(btc, 0.90), 2),
        },
        "note": "Historical break-even capex ceilings from market observations, before maintenance, taxes, financing and installation; not a purchase valuation.",
    }


def main():
    request = urllib.request.Request(MARKET_URL, headers={"User-Agent": "SYNPORA-Economic-Validation/1.0"})
    with urllib.request.urlopen(request, timeout=25) as response:
        raw = json.loads(response.read(5_000_001).decode("utf-8"))
    rows, rejected = observations_from_history(raw)
    if len(rows) < WINDOW + 2:
        raise SystemExit(f"Not enough provenance-verified market history: {len(rows)} usable, {rejected} rejected.")
    gaps = [(rows[i]["ts"] - rows[i - 1]["ts"]) / 60.0 for i in range(1, len(rows))]
    report = {
        "validation": "SYNPORA empirical economic validation",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": MARKET_URL,
        "history": {
            "received": len(raw) if isinstance(raw, list) else 0,
            "usable_external_observations": len(rows),
            "rejected_or_duplicate": rejected,
            "first_timestamp": datetime.fromtimestamp(rows[0]["ts"], timezone.utc).isoformat(),
            "last_timestamp": datetime.fromtimestamp(rows[-1]["ts"], timezone.utc).isoformat(),
            "median_gap_minutes": round(statistics.median(gaps), 2),
            "p90_gap_minutes": round(quantile(gaps, 0.90), 2),
        },
        "assumptions": {
            "risk_aversion": RISK_AVERSION,
            "empirical_bootstrap_samples_per_decision": BOOTSTRAP_SAMPLES,
            "history_window": WINDOW,
            "gpu_power_default_kw": 0.35,
            "gpu_utilization_default": 0.70,
            "gpu_platform_fee_default": 0.15,
            "btc_asic_efficiency_j_th": 20.0,
            "btc_pool_fee": 0.02,
            "gpu_capex_eur": 0.0,
            "btc_capex_eur": 0.0,
            "battery_value_eur_kwh": 0.071,
            "battery_round_trip_efficiency": 0.90,
            "battery_degradation_eur_kwh": 0.015,
            "grid_export_value_eur_kwh": 0.055,
            "tax_rate": 0.0,
            "tariffs_eur_kwh": TARIFFS,
        },
        "results": {},
        "limitations": [
            "A public market snapshot is not proof of an executed GPU rental, mined BTC payout, grid export settlement, or battery cycle.",
            "The model's zero-capex defaults can materially overstate compute/mining profitability.",
            "The Salzburg tariff scenarios use the published 2026 Strom Privat 24 variable all-in price components; the 1,500 kWh discount is a separate marginal-price case. The exact personal contract, discounts, metering and annual consumption are not confirmed.",
            "No hardware action, financial trade, or wallet transaction is performed.",
        ],
    }
    latest = rows[-1]
    for name, tariff in TARIFFS.items():
        latest_values = economics(latest, tariff)
        per_strategy = {
            strategy: distribution([economics(row, tariff)[strategy] for row in rows])
            for strategy in ("AI Compute", "BTC Mining", "Battery", "Grid")
        }
        report["results"][name] = {
            "tariff_eur_per_kwh": tariff,
            "latest_market_snapshot_economics": {
                key: round(value, 6) for key, value in latest_values.items()
            },
            "historical_strategy_distributions": per_strategy,
            "empirical_risk_aware_walk_forward": run_backtest(rows, tariff),
            "latest_snapshot_capex_hurdles": capex_hurdles(latest, tariff),\n            "historical_capex_hurdle_distribution": capex_hurdle_distribution(rows, tariff),
        }
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
