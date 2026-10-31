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
    # Current public tariff page on 2026-10-30: supplier energy component only.
    # The first 1,500 kWh rate is conditional on the 2026 household discount.
    "salzburg_2026_current_energy_only_standard": 0.2028,
    "salzburg_2026_current_energy_only_first_1500_discounted": 0.12,
    # Official product sheet explicitly says valid from 2026-11-01. Its total
    # variable price includes level-7 network charges/taxes in Salzburg Netz
    # GmbH's area; the first-1,500-kWh discount subtracts 8.28 ct from energy.
    # Metering charges, free days and annual fixed fees are excluded.
    "salzburg_2026_11_01_future_all_in_standard": 0.299351,
    "salzburg_2026_11_01_future_all_in_first_1500_discounted": 0.216551,
    # Transparent international sensitivity point, not the user's tariff.
    "low_cost_international_reference": 0.05,
}
WINDOW = 24
MAX_BACKTEST_GAP_MINUTES = 60.0
RISK_AVERSION_GRID = (0.0, 0.25, 0.50, 0.75, 1.0)
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
    quality = payload.get("data_quality")
    fields = quality.get("fields", {}) if isinstance(quality, dict) else {}
    if not isinstance(fields, dict):
        return False
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


def economics(row, tariff, gpu_capex=0.0, btc_capex=0.0, *,
              gpu_maintenance_eur_year=0.0, gpu_lifetime_years=5.0,
              gpu_uptime=0.98, btc_uptime=0.98, btc_maintenance_eur_year=0.0,
              btc_lifetime_years=4.0, gpu_utilization=None):
    """Conservative scenario economics in EUR per available site kWh.

    Public GPU marketplace asks are not realized payouts. Utilization, uptime,
    capex and maintenance are explicit scenario inputs, never silently assumed
    to be zero-cost investment economics.
    """
    # Hashprice is quoted per PH/s/day. 1 PH/s = 1,000 TH/s;
    # at 20 J/TH that is 20 kW. One kW is 0.05 PH/s, then /24 per kWh.
    btc_gross = (row["btc"] / row["fx"]) / (20.0 * 24.0)
    gpu_util = row["gpu_util"] if gpu_utilization is None else gpu_utilization
    gpu_gross = ((row["gpu"] / row["fx"]) * gpu_util
                 * (1.0 - row["gpu_fee"])) / row["gpu_power"]
    btc_fixed = (btc_capex / btc_lifetime_years + btc_maintenance_eur_year) / (3.5 * 8760.0)
    gpu_fixed = (gpu_capex / gpu_lifetime_years + gpu_maintenance_eur_year) / (row["gpu_power"] * 8760.0)
    # Apply the 2% pool fee once to mining revenue, not to electricity cost.
    btc = btc_uptime * (btc_gross * (1.0 - 0.02) - tariff) - btc_fixed
    # Marketplace fee is already included in gpu_gross. Availability scales
    # both operating revenue and energy consumption; no extra generic 2% fee.
    gpu = gpu_uptime * (gpu_gross - tariff) - gpu_fixed
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


def production_parametric_choice(current, tariff, seed, scenarios=200, shock=0.20):
    """Mirror decision_engine's independent mean-preserving lognormal shock model."""
    rng = random.Random(seed)
    energy_shock = shock * 0.65
    values = {name: [] for name in ("AI Compute", "BTC Mining", "Battery", "Grid")}
    for _ in range(scenarios):
        simulated = {
            **current,
            "btc": current["btc"] * rng.lognormvariate(-0.5 * shock * shock, shock),
            "gpu": current["gpu"] * rng.lognormvariate(-0.5 * shock * shock, shock),
        }
        simulated_tariff = tariff * rng.lognormvariate(
            -0.5 * energy_shock * energy_shock, energy_shock
        )
        for name, value in economics(simulated, simulated_tariff).items():
            values[name].append(value)
    scores = {}
    metrics = {}
    for name, series in values.items():
        ordered = sorted(series)
        mean = statistics.fmean(ordered)
        # Match decision_engine's current P05 index convention.
        p05 = ordered[max(0, int(0.05 * scenarios) - 1)]
        scores[name] = (1.0 - RISK_AVERSION) * mean + RISK_AVERSION * p05
        metrics[name] = {
            "mean": round(mean, 6),
            "p05": round(p05, 6),
            "probability_positive": round(sum(v > 0 for v in series) / scenarios, 4),
        }
    return max(scores, key=scores.get), metrics


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


def calibrated_shrinkage_weight(history, min_samples=24):
    """Estimate a bounded forecast correction from strictly prior settled pairs."""
    if len(history) < min_samples:
        return 0.0
    denominator = sum(delta * delta for delta, _ in history)
    if denominator <= 1e-12:
        return 0.0
    covariance = sum(delta * realized_delta for delta, realized_delta in history)
    # Ridge shrinkage dampens noisy corrections; never extrapolate beyond [0, 1].
    weight = covariance / (denominator * 1.10 + 1e-12)
    return max(0.0, min(1.0, weight))


def summarize_backtest(pairs, label):
    if not pairs:
        return {"status": "insufficient_data", "pairs": 0}
    regrets = [p["regret"] for p in pairs]
    errors = [abs(p["actual"] - p["predicted"]) for p in pairs]
    baseline_errors = [abs(p["actual"] - p["baseline_predicted"]) for p in pairs]
    brier_scores = [(p["predicted_win_probability"] - float(p["chosen"] == p["best"])) ** 2
                    for p in pairs if p.get("predicted_win_probability") is not None]
    strategy_means = {
        name: round(statistics.fmean(p["actual_values"][name] for p in pairs), 6)
        for name in ("AI Compute", "BTC Mining", "Battery", "Grid")
    }
    mae = statistics.fmean(errors)
    baseline_mae = statistics.fmean(baseline_errors)
    return {
        "model": label,
        "status": "evaluated" if len(pairs) >= 24 else "insufficient_data",
        "pairs": len(pairs),
        "mean_prediction_error_eur_per_kwh": round(mae, 6),
        "p90_prediction_error_eur_per_kwh": round(quantile(errors, 0.90), 6),
        "persistence_baseline_mae_eur_per_kwh": round(baseline_mae, 6),
        "mae_skill_vs_persistence": round(1.0 - mae / baseline_mae, 6) if baseline_mae > 1e-12 else None,
        "chosen_strategy_win_probability_brier": round(statistics.fmean(brier_scores), 6) if brier_scores else None,
        "mean_regret_eur_per_kwh": round(statistics.fmean(regrets), 6),
        "p90_regret_eur_per_kwh": round(quantile(regrets, 0.90), 6),
        "p95_regret_eur_per_kwh": round(quantile(regrets, 0.95), 6),
        "max_regret_eur_per_kwh": round(max(regrets), 6),
        "strategy_selection_accuracy": round(sum(p["chosen"] == p["best"] for p in pairs) / len(pairs), 4),
        "mean_next_snapshot_value_eur_per_kwh": strategy_means,
        "median_snapshot_gap_minutes": round(statistics.median(p["gap_minutes"] for p in pairs), 2),
        "p90_snapshot_gap_minutes": round(quantile([p["gap_minutes"] for p in pairs], 0.90), 2),
    }


def run_backtest(rows, tariff, min_history=WINDOW):
    empirical_pairs = []
    calibrated_pairs = []
    calibration_history = {name: [] for name in ("AI Compute", "BTC Mining", "Battery", "Grid")}
    calibration_weight_sums = {name: 0.0 for name in calibration_history}
    calibration_weight_counts = {name: 0 for name in calibration_history}
    parametric_pairs = []
    risk_sensitivity = {alpha: [] for alpha in RISK_AVERSION_GRID}
    skipped_long_gaps = 0
    # At t, train only on returns ending at t; score against the next eligible snapshot.
    # Exclude very long gaps: they are not a comparable one-step forecast horizon.
    for i in range(min_history, len(rows) - 1):
        decision = rows[i]
        next_row = rows[i + 1]
        gap_minutes = (next_row["ts"] - decision["ts"]) / 60.0
        if gap_minutes <= 0:
            continue
        if gap_minutes > MAX_BACKTEST_GAP_MINUTES:
            skipped_long_gaps += 1
            continue
        actual = economics(next_row, tariff)
        current_values = economics(decision, tariff)
        best = max(actual, key=actual.get)
        empirical = empirical_risk_choice(rows[:i + 1], decision, tariff, SEED + i)
        if empirical is not None:
            chosen, risk_metrics = empirical
            calibrated_values = {}
            for strategy in calibration_history:
                weight = calibrated_shrinkage_weight(calibration_history[strategy])
                model_delta = risk_metrics[strategy]["mean"] - current_values[strategy]
                calibrated_values[strategy] = current_values[strategy] + weight * model_delta
                calibration_weight_sums[strategy] += weight
                calibration_weight_counts[strategy] += 1
            calibrated_pairs.append({
                "chosen": chosen,
                "predicted": calibrated_values[chosen],
                "predicted_win_probability": risk_metrics[chosen]["win_probability"],
                "baseline_predicted": current_values[chosen],
                "actual": actual[chosen],
                "best": best,
                "regret": max(0.0, actual[best] - actual[chosen]),
                "actual_values": actual,
                "gap_minutes": gap_minutes,
            })
            # Settle only after forecasting; these residuals calibrate later decisions.
            for strategy in calibration_history:
                model_delta = risk_metrics[strategy]["mean"] - current_values[strategy]
                realized_delta = actual[strategy] - current_values[strategy]
                calibration_history[strategy].append((model_delta, realized_delta))
            for alpha in RISK_AVERSION_GRID:
                alpha_scores = {
                    name: (1.0 - alpha) * metric["mean"] + alpha * metric["p05"]
                    for name, metric in risk_metrics.items()
                }
                alpha_choice = max(alpha_scores, key=alpha_scores.get)
                risk_sensitivity[alpha].append({
                    "chosen": alpha_choice,
                    "best": best,
                    "regret": max(0.0, actual[best] - actual[alpha_choice]),
                })
            empirical_pairs.append({
                "chosen": chosen,
                "predicted": risk_metrics[chosen]["mean"],
                "predicted_win_probability": risk_metrics[chosen]["win_probability"],
                "baseline_predicted": current_values[chosen],
                "actual": actual[chosen],
                "best": best,
                "regret": max(0.0, actual[best] - actual[chosen]),
                "actual_values": actual,
                "gap_minutes": gap_minutes,
            })
        parametric, parametric_metrics = production_parametric_choice(
            decision, tariff, 42 + i, scenarios=200, shock=0.20
        )
        parametric_pairs.append({
            "chosen": parametric,
            "predicted": parametric_metrics[parametric]["mean"],
            "predicted_win_probability": None,
            "baseline_predicted": current_values[parametric],
            "actual": actual[parametric],
            "best": best,
            "regret": max(0.0, actual[best] - actual[parametric]),
            "actual_values": actual,
            "gap_minutes": gap_minutes,
        })
    warnings = [
        "Both tests are out-of-sample next-snapshot market mark-to-model tests, not realized cashflow.",
        "Forecast MAE is compared with a same-strategy persistence baseline; positive skill means improvement over carrying forward the current snapshot value.",
        "The empirical paired bootstrap preserves observed BTC/GPU/FX co-movement; it does not prove long-horizon stationarity.",
        "Risk-aversion sensitivity is diagnostic across fixed alpha values, not an automatic production parameter change.",
        "The parametric comparator mirrors the production decision engine's independent 20% lognormal BTC/GPU shock and 13% energy shock.",
        "Battery and grid values remain explicit model defaults (7.1 ct/kWh battery reference, 5.5 ct/kWh export reference).",
        "Equipment capex, maintenance, taxes, metering, connection limits and actual uptime are not supplied by the production market feed.",
        f"Next-snapshot pairs with gaps over {MAX_BACKTEST_GAP_MINUTES:g} minutes are excluded from the one-step comparison.",
    ]
    risk_summary = {}
    for alpha, pairs in risk_sensitivity.items():
        if pairs:
            regrets = [p["regret"] for p in pairs]
            risk_summary[str(alpha)] = {
                "pairs": len(pairs),
                "mean_regret_eur_per_kwh": round(statistics.fmean(regrets), 6),
                "p90_regret_eur_per_kwh": round(quantile(regrets, 0.90), 6),
                "selection_accuracy": round(sum(p["chosen"] == p["best"] for p in pairs) / len(pairs), 4),
            }
        else:
            risk_summary[str(alpha)] = {"pairs": 0, "status": "insufficient_data"}
    return {
        "production_parametric_monte_carlo": summarize_backtest(parametric_pairs, "production_independent_lognormal"),
        "independent_empirical_paired_bootstrap": summarize_backtest(empirical_pairs, "historical_paired_bootstrap"),
        "walk_forward_shrunk_forecast": summarize_backtest(calibrated_pairs, "prior_error_calibrated_shrinkage"),
        "forecast_calibration": {
            "method": "bounded ridge-shrunk correction to persistence baseline, fitted only on earlier settled one-step forecasts",
            "minimum_prior_pairs_before_nonzero_correction": 24,
            "mean_correction_weight_by_strategy": {
                name: round(calibration_weight_sums[name] / calibration_weight_counts[name], 6)
                if calibration_weight_counts[name] else 0.0
                for name in calibration_history
            },
        },
        "risk_aversion_sensitivity": {
            "purpose": "diagnostic only; do not auto-change production risk aversion from this same holdout",
            "current_production_alpha": RISK_AVERSION,
            "results": risk_summary,
        },
        "excluded_long_gap_pairs": skipped_long_gaps,
        "maximum_accepted_pair_gap_minutes": MAX_BACKTEST_GAP_MINUTES,
        "warnings": warnings,
    }


def capex_hurdles(latest, tariff):
    # Same PH/s-to-kWh conversion as economics(): 1 PH/s draws 20 kW.
    btc_gross = (latest["btc"] / latest["fx"]) / (20.0 * 24.0)
    gpu_gross = ((latest["gpu"] / latest["fx"]) * latest["gpu_util"]
                 * (1.0 - latest["gpu_fee"])) / latest["gpu_power"]
    btc_margin = 0.98 * (btc_gross * (1.0 - 0.02) - tariff)  # 98% uptime and 2% pool fee
    gpu_margin = 0.98 * (gpu_gross - tariff)  # 98% uptime; no extra generic fee
    return {
        "gpu_capex_break_even_eur_at_5y_no_maintenance": round(
            max(0.0, gpu_margin) * latest["gpu_power"] * 8760 * 5, 2),
        "btc_capex_break_even_eur_at_4y_no_maintenance": round(
            max(0.0, btc_margin) * 3.5 * 8760 * 4, 2),
    }


def conservative_gpu_sensitivity(rows, tariff):
    """Stress GPU economics with non-zero capex/maintenance and lower utilization."""
    cases = [
        {"name": "low_utilization_costed", "utilization": 0.30, "capex_eur": 10000.0, "maintenance_eur_year": 750.0},
        {"name": "mid_utilization_costed", "utilization": 0.50, "capex_eur": 10000.0, "maintenance_eur_year": 750.0},
        {"name": "high_utilization_costed", "utilization": 0.70, "capex_eur": 10000.0, "maintenance_eur_year": 750.0},
    ]
    result = {}
    for case in cases:
        values = [economics(row, tariff, gpu_capex=case["capex_eur"],
                            gpu_maintenance_eur_year=case["maintenance_eur_year"],
                            gpu_lifetime_years=5.0, gpu_uptime=0.95,
                            gpu_utilization=case["utilization"])["AI Compute"]
                  for row in rows]
        result[case["name"]] = {
            "utilization_assumption": case["utilization"],
            "capex_eur": case["capex_eur"],
            "maintenance_eur_year": case["maintenance_eur_year"],
            "uptime_assumption": 0.95,
            "five_year_lifetime": True,
            "modeled_contribution_distribution": distribution(values),
            "realized_cashflow_validated": False,
        }
    return result


def investment_readiness():
    return {
        "status": "not_investment_validated",
        "economic_basis": "external_market_quotes_to_model",
        "realized_cashflow_validated": False,
        "confidence_calibrated": False,
        "missing_evidence": [
            "actual GPU rental bookings, occupancy and settled payouts",
            "actual BTC pool settlements and fees",
            "metered all-in electricity invoices and export settlements",
            "hardware purchase invoices, installation and financing costs",
            "maintenance, cooling, facility overhead, taxes and insurance",
            "battery charge/discharge meter data and degradation",
        ],
        "zero_cost_defaults_are_not_investment_inputs": True,
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
        "investment_readiness": investment_readiness(),
        "limitations": [
            "A public market snapshot is not proof of an executed GPU rental, mined BTC payout, grid export settlement, or battery cycle.",
            "The model's zero-capex defaults can materially overstate compute/mining profitability.",
            "The 20.28 ct/kWh standard and 12.00 ct/kWh discounted supplier-energy rates are the current public Strom Privat 24 page values on 2026-10-30; they exclude network charges, taxes and duties and are not all-in dispatch prices. The 29.9351/21.6551 ct/kWh all-in cases come from the linked product sheet explicitly valid from 2026-11-01, and apply only to level-7 charges in the Salzburg Netz GmbH area. They may not match another distribution area. The 1,500-kWh discount is conditional on eligible natural-person household contracts. Metering charges, free-day discounts and annual fixed fees are excluded.",
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
            "latest_snapshot_capex_hurdles": capex_hurdles(latest, tariff),
            "historical_capex_hurdle_distribution": capex_hurdle_distribution(rows, tariff),
            "conservative_gpu_cost_sensitivity": conservative_gpu_sensitivity(rows, tariff),
        }
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
