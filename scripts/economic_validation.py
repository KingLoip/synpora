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

MARKET_URL = "https://synpora-production.up.railway.app/api/v1/market/history?limit=10000&compact=true"
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
        quality = payload.get("data_quality", {})
        fields = quality.get("fields", {}) if isinstance(quality, dict) else {}
        def optional(key, default, lower=0.000001, upper=None):
            value = finite(payload.get(key))
            if value is None or value < lower or (upper is not None and value > upper):
                return default
            return value
        def optional_external(key):
            field = fields.get(key) if isinstance(fields, dict) else None
            value = finite(payload.get(key))
            age = finite(field.get("age_seconds")) if isinstance(field, dict) else None
            if (not isinstance(field, dict) or field.get("source") != "external"
                    or field.get("available") is not True or value is None or value <= 0
                    or age is None or age < -60 or age > 900):
                return None
            return value
        result.append({
            "ts": ts,
            "btc": float(payload["btc_hashprice_usd_ph_day"]),
            "btc_price": optional_external("btc_price_usd"),
            "network_hashrate": optional_external("network_hashrate_eh"),
            "btc_difficulty": optional_external("btc_difficulty"),
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
              btc_lifetime_years=4.0, gpu_utilization=None,
              btc_asic_efficiency_j_th=20.0, btc_device_power_kw=3.5,
              btc_facility_overhead_kw=0.0, btc_pool_fee=0.02,
              btc_tax_rate=0.0):
    """Conservative scenario economics in EUR per available site kWh.

    Public GPU marketplace asks are not realized payouts. Utilization, uptime,
    capex and maintenance are explicit scenario inputs, never silently assumed
    to be zero-cost investment economics.
    """
    # Hashprice is USD per PH/s/day. At efficiency E J/TH, one kW
    # produces 1/E PH/s; divide daily revenue by 24 for EUR per kWh.
    # Facility overhead consumes site energy but earns no additional hashrate.
    device_power = max(0.01, float(btc_device_power_kw))
    facility_overhead = max(0.0, float(btc_facility_overhead_kw))
    site_power = device_power + facility_overhead
    efficiency = max(0.01, float(btc_asic_efficiency_j_th))
    pool_fee = max(0.0, min(0.99, float(btc_pool_fee)))
    btc_gross = ((row["btc"] / row["fx"]) / (efficiency * 24.0)
                 * (device_power / site_power))
    gpu_util = row["gpu_util"] if gpu_utilization is None else gpu_utilization
    gpu_gross = ((row["gpu"] / row["fx"]) * gpu_util
                 * (1.0 - row["gpu_fee"])) / row["gpu_power"]
    btc_fixed = (btc_capex / btc_lifetime_years + btc_maintenance_eur_year) / (site_power * 8760.0)
    gpu_fixed = (gpu_capex / gpu_lifetime_years + gpu_maintenance_eur_year) / (row["gpu_power"] * 8760.0)
    # Pool fees apply to mining revenue only; uptime applies to both revenue
    # and variable energy cost. Annual fixed costs are spread over site capacity.
    btc_pre_tax = btc_uptime * (btc_gross * (1.0 - pool_fee) - tariff) - btc_fixed
    btc = btc_pre_tax - max(0.0, btc_pre_tax) * max(0.0, min(1.0, btc_tax_rate))
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
    best_counts = {
        name: sum(p["best"] == name for p in pairs)
        for name in ("AI Compute", "BTC Mining", "Battery", "Grid")
    }
    dominant_share = max(best_counts.values()) / len(pairs)
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
        "best_strategy_counts": best_counts,
        "selection_accuracy_information": "low_information_single_strategy_dominance" if dominant_share >= 0.95 else "multi_strategy_observations",
        "mean_next_snapshot_value_eur_per_kwh": strategy_means,
        "median_snapshot_gap_minutes": round(statistics.median(p["gap_minutes"] for p in pairs), 2),
        "p90_snapshot_gap_minutes": round(quantile([p["gap_minutes"] for p in pairs], 0.90), 2),
    }


def _candidate_stable_across_blocks(model_errors, baseline_errors, blocks=4):
    n = min(len(model_errors), len(baseline_errors))
    if n < blocks * 8:
        return False
    block_size = n // blocks
    positive = 0
    for block in range(blocks):
        start = block * block_size
        end = n if block == blocks - 1 else (block + 1) * block_size
        model_mae = statistics.fmean(model_errors[start:end])
        baseline_mae = statistics.fmean(baseline_errors[start:end])
        if baseline_mae > 1e-12 and model_mae < baseline_mae:
            positive += 1
    return positive == blocks



def candidate_holdout_skill(model_errors, baseline_errors, min_training_skill=0.15,
                            min_validation_skill=0.05):
    """Split recent rolling-origin errors into model-selection and untouched holdout blocks."""
    n = min(len(model_errors), len(baseline_errors))
    split = int(n * 0.75)
    if split < 24 or n - split < 8:
        return {
            "eligible": False, "training_skill": None, "validation_skill": None,
            "training_pairs": split, "validation_pairs": n - split,
        }
    train_model = statistics.fmean(model_errors[:split])
    train_baseline = statistics.fmean(baseline_errors[:split])
    valid_model = statistics.fmean(model_errors[split:n])
    valid_baseline = statistics.fmean(baseline_errors[split:n])
    training_skill = 1.0 - train_model / train_baseline if train_baseline > 1e-12 else None
    validation_skill = 1.0 - valid_model / valid_baseline if valid_baseline > 1e-12 else None
    return {
        "eligible": bool(training_skill is not None and validation_skill is not None
                         and training_skill >= min_training_skill
                         and validation_skill >= min_validation_skill),
        "training_skill": round(training_skill, 6) if training_skill is not None else None,
        "validation_skill": round(validation_skill, 6) if validation_skill is not None else None,
        "training_pairs": split,
        "validation_pairs": n - split,
    }


def _horizon_candidate_values(values, origin, sample_steps, interval_seconds):
    """Use elapsed-time windows and phase-aligned prior seasonal observations."""
    interval = max(1.0, float(interval_seconds))
    period_24h = max(1, round(24.0 * 3600.0 / interval))
    period_168h = max(1, round(168.0 * 3600.0 / interval))
    recent_steps = max(2, round(48.0 * 3600.0 / interval))
    lookback = max(period_168h, recent_steps, period_24h)
    history = values[max(0, origin - lookback + 1):origin + 1]
    recent = history[-min(len(history), recent_steps):]
    daily = history[-min(len(history), period_24h):]
    last = float(values[origin])

    def ewma(series, alpha):
        value = float(series[0])
        for item in series[1:]:
            value = alpha * float(item) + (1.0 - alpha) * value
        return value

    def seasonal(period):
        lag = ((max(1, int(sample_steps)) + period - 1) // period) * period
        index = origin + int(sample_steps) - lag
        return float(values[index]) if 0 <= index <= origin else last

    slope = (float(recent[-1]) - float(recent[0])) / (len(recent) - 1) if len(recent) > 1 else 0.0
    decay_steps = max(1.0, 6.0 * 3600.0 / interval)
    damped_steps = decay_steps * (1.0 - math.exp(-max(1.0, float(sample_steps)) / decay_steps))
    return {
        "recent_mean": statistics.fmean(float(v) for v in recent),
        "last_value": last,
        "trend": last + slope * damped_steps,
        "mean_24": statistics.fmean(float(v) for v in daily),
        "median_24": statistics.median(float(v) for v in daily),
        "seasonal_24h": seasonal(period_24h),
        "seasonal_168h": seasonal(period_168h),
        "ewma_02": ewma(recent, 0.2),
        "ewma_05": ewma(recent, 0.5),
    }


def _btc_price_network_candidate(hashprice_values, price_values, network_values,
                                 origin, sample_steps, interval_seconds):
    """Structural hashprice candidate from BTC price and network hash rate/difficulty."""
    interval = max(1.0, float(interval_seconds))
    recent_steps = max(2, round(48.0 * 3600.0 / interval))
    start = max(0, origin - recent_steps + 1)
    price_history = price_values[start:origin + 1]
    network_history = network_values[start:origin + 1]
    if (len(price_history) < 2 or len(network_history) < 2
            or any(v is None or not math.isfinite(float(v)) or float(v) <= 0 for v in price_history)
            or any(v is None or not math.isfinite(float(v)) or float(v) <= 0 for v in network_history)):
        return None
    horizon_hours = max(0.0, float(sample_steps) * interval / 3600.0)
    damped_hours = min(horizon_hours, 24.0) * 0.25

    def damped(series):
        logs = [math.log(float(v)) for v in series]
        elapsed_hours = max(1e-9, (len(logs) - 1) * interval / 3600.0)
        slope = (logs[-1] - logs[0]) / elapsed_hours
        return math.exp(max(-50.0, min(50.0, logs[-1] + slope * damped_hours)))

    predicted_price = damped(price_history)
    predicted_network = damped(network_history)
    current_price = float(price_values[origin])
    current_network = float(network_values[origin])
    current_hashprice = float(hashprice_values[origin])
    if min(current_price, current_network, predicted_price, predicted_network) <= 0:
        return None
    return max(0.0, current_hashprice * (predicted_price / current_price)
               * (current_network / predicted_network))


def corrected_forecast_weights(values, sample_steps=1, interval_seconds=300.0, log_scale=False,
                                btc_price_values=None, network_values=None):
    """Select same-horizon candidates on horizon-spaced past-only origins and a strict holdout."""
    direct_names = ("recent_mean", "last_value", "trend", "mean_24", "median_24",
                    "seasonal_24h", "seasonal_168h", "ewma_02", "ewma_05")
    exogenous_ready = (
        btc_price_values is not None and network_values is not None
        and len(btc_price_values) == len(values) and len(network_values) == len(values)
        and all(v is not None and math.isfinite(float(v)) and float(v) > 0 for v in btc_price_values)
        and all(v is not None and math.isfinite(float(v)) and float(v) > 0 for v in network_values)
    )
    names = direct_names + (("btc_price_network_structural",) if exogenous_ready else ())
    fallback = {name: (1.0 if name == "last_value" else 0.0) for name in names}
    interval = max(1.0, float(interval_seconds))
    weekly_steps = max(1, round(168.0 * 3600.0 / interval))
    if sample_steps < 1 or len(values) < weekly_steps + sample_steps + 32:
        return fallback
    model_values = [math.log(max(float(v), 1e-12)) for v in values] if log_scale else [float(v) for v in values]
    errors = {name: [] for name in names}
    origin_stride = max(1, sample_steps // 24)
    first = max(weekly_steps, len(values) - sample_steps - 120 * origin_stride)
    for origin in range(first, len(values) - sample_steps, origin_stride):
        candidates = _horizon_candidate_values(model_values, origin, sample_steps, interval)
        if log_scale:
            candidates = {name: math.exp(max(-50.0, min(50.0, value)))
                          for name, value in candidates.items()}
        if exogenous_ready:
            structural = _btc_price_network_candidate(
                values, btc_price_values, network_values, origin, sample_steps, interval)
            if structural is not None:
                candidates["btc_price_network_structural"] = structural
        actual = float(values[origin + sample_steps])
        for name in names:
            if name in candidates:
                errors[name].append(abs(actual - candidates[name]))
    count = len(errors["last_value"])
    split = int(count * 0.75)
    if split < 24 or count - split < 8:
        return fallback
    train_maes = {name: statistics.fmean(series[:split]) for name, series in errors.items()}
    best = min(train_maes, key=train_maes.get)
    if best != "last_value":
        # Match the production selector exactly: model choice is trained on the
        # first 75% of the same 120 horizon-spaced origins, then retained only
        # when the untouched final 25% improves persistence by >=5% in all
        # four chronological holdout blocks. Confidence intervals are computed
        # separately on the final outer backtest, never used to alter this
        # per-origin production-equivalent model selection.
        valid_model = errors[best][split:]
        valid_base = errors["last_value"][split:]
        baseline_mae = statistics.fmean(valid_base)
        candidate_mae = statistics.fmean(valid_model)
        holdout_skill = (1.0 - candidate_mae / baseline_mae
                         if baseline_mae > 1e-12 else None)
        block_size = max(1, len(valid_model) // 4)
        stable = True
        for block in range(4):
            start = block * block_size
            end = len(valid_model) if block == 3 else min(len(valid_model), (block + 1) * block_size)
            if (end <= start
                    or statistics.fmean(valid_model[start:end])
                    >= statistics.fmean(valid_base[start:end])):
                stable = False
                break
        if holdout_skill is None or holdout_skill < 0.05 or not stable:
            best = "last_value"
    return {name: (1.0 if name == best else 0.0) for name in names}


def corrected_forecast_value(values, weights, horizon_hours, interval_seconds, log_scale=False,
                              btc_price_values=None, network_values=None):
    """Apply the production candidate family and invert the BTC log transform."""
    interval = max(1.0, float(interval_seconds))
    sample_steps = max(1, round(horizon_hours * 3600.0 / interval))
    model_values = [math.log(max(float(v), 1e-12)) for v in values] if log_scale else [float(v) for v in values]
    candidates = _horizon_candidate_values(model_values, len(values) - 1, sample_steps, interval)
    if log_scale:
        candidates = {name: math.exp(max(-50.0, min(50.0, value)))
                      for name, value in candidates.items()}
    if "btc_price_network_structural" in weights and btc_price_values is not None and network_values is not None:
        structural = _btc_price_network_candidate(
            values, btc_price_values, network_values, len(values) - 1, sample_steps, interval)
        if structural is not None:
            candidates["btc_price_network_structural"] = structural
    return max(0.0, sum(weights[name] * candidates.get(name, float(values[-1])) for name in weights))


def _block_bootstrap_skill_ci(model_errors, baseline_errors, seed, block_length=6, replicates=BOOTSTRAP_SAMPLES):
    n = min(len(model_errors), len(baseline_errors))
    if n < 30:
        return {"lower_95": None, "upper_95": None, "replicates": 0}
    rng = random.Random(seed)
    skills = []
    for _ in range(replicates):
        indices = []
        while len(indices) < n:
            start = rng.randrange(n)
            indices.extend((start + offset) % n for offset in range(block_length))
        indices = indices[:n]
        model_mae = statistics.fmean(model_errors[i] for i in indices)
        baseline_mae = statistics.fmean(baseline_errors[i] for i in indices)
        if baseline_mae > 1e-12:
            skills.append(1.0 - model_mae / baseline_mae)
    return {
        "lower_95": round(quantile(skills, 0.025), 6) if skills else None,
        "upper_95": round(quantile(skills, 0.975), 6) if skills else None,
        "replicates": len(skills),
    }



def horizon_release_gate(horizons, required_horizons=(1, 6, 24, 72)):
    """Require every advertised horizon to meet the same independent release bar."""
    failed = [
        horizon for horizon in required_horizons
        if not isinstance(horizons.get(str(horizon)), dict)
        or horizons[str(horizon)].get("release_eligible") is not True
    ]
    return {
        "status": "pass" if not failed else "blocked",
        "required_horizons_hours": list(required_horizons),
        "ineligible_horizons_hours": failed,
    }


_PRODUCTION_FORECAST_CACHE = {}


def production_corrected_horizon_validation(rows, tariff, horizons_hours=(1, 6, 24, 72)):
    """Validate the corrected production forecast at the API's actual hour horizons."""
    results = {}
    all_gaps = [rows[j]["ts"] - rows[j - 1]["ts"] for j in range(1, len(rows))
                if rows[j]["ts"] > rows[j - 1]["ts"]]
    median_interval_seconds = statistics.median(all_gaps) if all_gaps else 3600.0
    for horizon in horizons_hours:
        strategy_errors = {"BTC Mining": [], "AI Compute": []}
        baseline_errors = {"BTC Mining": [], "AI Compute": []}
        combined_errors = []
        combined_baseline_errors = []
        skipped = 0
        insufficient_history_skipped = 0
        origin_stride = max(1, len(rows) // 200)
        for i in range(23, len(rows) - 1, origin_stride):
            decision = rows[i]
            expected_seconds = horizon * 3600.0
            target_index = min(
                range(i + 1, len(rows)),
                key=lambda j: abs((rows[j]["ts"] - decision["ts"]) - expected_seconds),
            )
            target = rows[target_index]
            actual_gap = target["ts"] - decision["ts"]
            if abs(actual_gap - expected_seconds) > max(300.0, expected_seconds * 0.10):
                skipped += 1
                continue
            history_rows = rows[:i + 1]
            recent_rows = history_rows[-48:]
            gaps = [recent_rows[j]["ts"] - recent_rows[j - 1]["ts"]
                    for j in range(1, len(recent_rows))
                    if recent_rows[j]["ts"] > recent_rows[j - 1]["ts"]]
            interval_seconds = statistics.median(gaps) if gaps else median_interval_seconds
            sample_steps = max(1, round(horizon * 3600.0 / interval_seconds))
            weekly_steps = max(1, round(168.0 * 3600.0 / interval_seconds))
            # Persistence fallback is safe operationally, but is not evidence
            # that the learned horizon model is validated.
            if len(history_rows) < weekly_steps + sample_steps + 32:
                insufficient_history_skipped += 1
                continue
            cache_key = (id(rows), int(horizon), int(i))
            cached_forecast = _PRODUCTION_FORECAST_CACHE.get(cache_key)
            if cached_forecast is None:
                btc_values = [row["btc"] for row in history_rows]
                gpu_values = [row["gpu"] for row in history_rows]
                btc_price_values = [row.get("btc_price") for row in history_rows]
                network_values = [row.get("network_hashrate") for row in history_rows]
                if not all(v is not None and v > 0 for v in network_values):
                    network_values = [row.get("btc_difficulty") for row in history_rows]
                if not all(v is not None and v > 0 for v in network_values):
                    network_values = None
                if not all(v is not None and v > 0 for v in btc_price_values):
                    btc_price_values = None
                btc_weights = corrected_forecast_weights(
                    btc_values, sample_steps, interval_seconds, log_scale=True,
                    btc_price_values=btc_price_values, network_values=network_values)
                gpu_weights = corrected_forecast_weights(gpu_values, sample_steps, interval_seconds)
                cached_forecast = (
                    corrected_forecast_value(
                        btc_values, btc_weights, horizon, interval_seconds, log_scale=True,
                        btc_price_values=btc_price_values, network_values=network_values),
                    corrected_forecast_value(
                        gpu_values, gpu_weights, horizon, interval_seconds),
                )
                _PRODUCTION_FORECAST_CACHE[cache_key] = cached_forecast
            predicted = dict(decision)
            predicted["btc"], predicted["gpu"] = cached_forecast
            # Market-price forecasts are independent of the site tariff, so reuse
            # them across tariff scenarios without re-fitting the same models.
            # The live API holds the supplied EUR/USD input constant across the forecast.
            actual_values = economics(target, tariff)
            predicted_values = economics(predicted, tariff)
            persistence_values = economics(decision, tariff)
            pair_model = []
            pair_baseline = []
            for strategy in ("BTC Mining", "AI Compute"):
                strategy_errors[strategy].append(
                    abs(actual_values[strategy] - predicted_values[strategy]))
                baseline_errors[strategy].append(
                    abs(actual_values[strategy] - persistence_values[strategy]))
                pair_model.append(strategy_errors[strategy][-1])
                pair_baseline.append(baseline_errors[strategy][-1])
            combined_errors.append(statistics.fmean(pair_model))
            combined_baseline_errors.append(statistics.fmean(pair_baseline))
        pairs = len(combined_errors)
        model_mae = statistics.fmean(combined_errors) if pairs else None
        baseline_mae = statistics.fmean(combined_baseline_errors) if pairs else None
        skill = (1.0 - model_mae / baseline_mae
                 if model_mae is not None and baseline_mae is not None and baseline_mae > 1e-12
                 else None)
        per_strategy = {}
        for strategy in ("BTC Mining", "AI Compute"):
            model_series = strategy_errors[strategy]
            baseline_series = baseline_errors[strategy]
            model_series_mae = statistics.fmean(model_series) if model_series else None
            baseline_series_mae = statistics.fmean(baseline_series) if baseline_series else None
            per_strategy[strategy] = {
                "mae_eur_per_kwh": round(model_series_mae, 6) if model_series_mae is not None else None,
                "persistence_mae_eur_per_kwh": round(baseline_series_mae, 6) if baseline_series_mae is not None else None,
                "mae_skill_vs_persistence": round(1.0 - model_series_mae / baseline_series_mae, 6)
                if model_series_mae is not None and baseline_series_mae and baseline_series_mae > 1e-12 else None,
            }
        all_gaps = [rows[j]["ts"] - rows[j - 1]["ts"] for j in range(1, len(rows))
                    if rows[j]["ts"] > rows[j - 1]["ts"]]
        median_interval = statistics.median(all_gaps) if all_gaps else 3600.0
        block_length = min(48, max(6, math.ceil(
            (horizon * 3600.0) / (median_interval * origin_stride))))
        ci = _block_bootstrap_skill_ci(
            combined_errors, combined_baseline_errors, SEED + horizon,
            block_length=block_length,
        )
        nonnegative = all(
            value["mae_skill_vs_persistence"] is not None
            and value["mae_skill_vs_persistence"] >= 0.0
            for value in per_strategy.values()
        )
        results[str(horizon)] = {
            "horizon_hours": horizon,
            "pairs": pairs,
            "skipped_nonmatching_horizon": skipped,
            "skipped_insufficient_model_history": insufficient_history_skipped,
            "model_history_required_observations": max(1, round(168.0 * 3600.0 / median_interval_seconds)) + max(1, round(horizon * 3600.0 / median_interval_seconds)) + 32,
            "mae_eur_per_kwh": round(model_mae, 6) if model_mae is not None else None,
            "persistence_mae_eur_per_kwh": round(baseline_mae, 6) if baseline_mae is not None else None,
            "mae_skill_vs_persistence": round(skill, 6) if skill is not None else None,
            "mae_skill_confidence_interval_95": ci,
            "bootstrap_block_length": block_length,
            "per_strategy": per_strategy,
            "release_eligible": bool(
                pairs >= 100 and skill is not None and skill >= 0.05
                and ci["lower_95"] is not None and ci["lower_95"] > 0.0
                and nonnegative
            ),
        }
    release_gate = horizon_release_gate(results, horizons_hours)
    return {
        "method": "rolling_origin_same_features_as_production_with_hourly_horizon_and_damped_trend",
        "status": release_gate["status"],
        "minimum_pairs": 100,
        "minimum_mae_skill_vs_persistence": 0.05,
        "horizons": results,
        "required_horizons_hours": list(horizons_hours),
        "release_gate": release_gate,
        "release_eligible": release_gate["status"] == "pass",
        "warning": "Global market quotes and constant tariff; not realized payouts or site-specific investment validation.",
    }


def run_backtest(rows, tariff, min_history=WINDOW):
    forecast_history_rows = rows
    # Keep one-step risk/regret work bounded to the latest 2,000 snapshots;
    # long-horizon validation still receives the full 10,000-row history.
    rows = rows[-2000:]
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
    calibrated_summary = summarize_backtest(calibrated_pairs, "prior_error_calibrated_shrinkage")
    calibrated_skill = calibrated_summary.get("mae_skill_vs_persistence")
    forecast_gate = {
        "status": "pass" if calibrated_summary.get("pairs", 0) >= 100 and calibrated_skill is not None and calibrated_skill >= 0.05 else "blocked",
        "minimum_pairs": 100,
        "minimum_mae_skill_vs_persistence": 0.05,
        "observed_mae_skill_vs_persistence": calibrated_skill,
        "reason": "Forecasts must beat persistence by at least 5% on 100+ out-of-sample pairs; current evidence does not meet this bar."
    }
    return {
        "production_corrected_horizon_validation": production_corrected_horizon_validation(forecast_history_rows, tariff),
        "forecast_quality_gate": forecast_gate,
        "production_parametric_monte_carlo": summarize_backtest(parametric_pairs, "production_independent_lognormal"),
        "independent_empirical_paired_bootstrap": summarize_backtest(empirical_pairs, "historical_paired_bootstrap"),
        "walk_forward_shrunk_forecast": calibrated_summary,
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


def btc_mining_sensitivity(rows, tariff):
    """Show BTC economics under explicit illustrative ASIC cost scenarios."""
    cases = {
        "production_reference_inputs": {},
        "efficient_asic_17j_th": {"btc_asic_efficiency_j_th": 17.0},
        "older_asic_25j_th": {"btc_asic_efficiency_j_th": 25.0},
        "facility_overhead_0_15kw": {"btc_facility_overhead_kw": 0.15},
        "costed_asic_5000_eur": {
            "btc_capex": 5000.0, "btc_maintenance_eur_year": 300.0,
            "btc_lifetime_years": 4.0, "btc_facility_overhead_kw": 0.15,
            "btc_uptime": 0.95, "btc_pool_fee": 0.03,
        },
        "conservative_asic_8000_eur": {
            "btc_capex": 8000.0, "btc_maintenance_eur_year": 500.0,
            "btc_lifetime_years": 4.0, "btc_asic_efficiency_j_th": 25.0,
            "btc_device_power_kw": 3.5, "btc_facility_overhead_kw": 0.25,
            "btc_uptime": 0.95, "btc_pool_fee": 0.03,
        },
    }
    result = {}
    for name, assumptions in cases.items():
        values = [economics(row, tariff, **assumptions)["BTC Mining"] for row in rows]
        result[name] = {
            "assumptions": assumptions,
            "net_contribution_eur_per_available_site_kwh": distribution(values),
            "positive_contribution_share": round(sum(value > 0 for value in values) / len(values), 4) if values else None,
            "illustrative_only": name != "production_reference_inputs",
            "realized_cashflow_validated": False,
        }
    return {
        "method": "sensitivity_over_efficiency_overhead_uptime_pool_fee_capex_and_maintenance",
        "cases": result,
        "warning": "Illustrative ASIC purchase and maintenance assumptions are not user-confirmed quotes or verified pool settlements.",
    }


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
        raw = json.loads(response.read(20_000_001).decode("utf-8"))
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
            "btc_device_power_kw": 3.5,
            "btc_facility_overhead_kw": 0.0,
            "btc_uptime": 0.98,
            "btc_price_external_observations": sum(row.get("btc_price") is not None for row in rows),
            "network_hashrate_external_observations": sum(row.get("network_hashrate") is not None for row in rows),
            "difficulty_external_observations": sum(row.get("btc_difficulty") is not None for row in rows),
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
            "btc_mining_sensitivity": btc_mining_sensitivity(rows, tariff),
        }
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
