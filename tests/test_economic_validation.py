import importlib.util
import inspect
import math
import unittest
from unittest.mock import patch
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "economic_validation", Path(__file__).resolve().parents[1] / "scripts" / "economic_validation.py"
)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def external_field(age):
    return {"source": "external", "available": True, "age_seconds": age}


class EconomicValidationTests(unittest.TestCase):
    def test_market_history_validation_requests_compact_extended_history(self):
        self.assertIn("limit=10000&compact=true", MODULE.MARKET_URL)

    def test_history_parser_keeps_only_fresh_external_btc_price_and_hashrate(self):
        payload = {
            "btc_hashprice_usd_ph_day": 40,
            "gpu_l40s_usd_hour": 1.1,
            "eur_usd": 1.1,
            "btc_price_usd": 60000,
            "network_hashrate_eh": 500,
            "btc_difficulty": 90,
            "data_quality": {"fields": {
                "btc_hashprice_usd_ph_day": external_field(10),
                "gpu_l40s_usd_hour": external_field(10),
                "eur_usd": external_field(10),
                "btc_price_usd": external_field(10),
                "network_hashrate_eh": external_field(10),
                "btc_difficulty": external_field(10),
            }},
        }
        rows, rejected = MODULE.observations_from_history([{"timestamp": 100, "data": payload}])
        self.assertEqual(rejected, 0)
        self.assertEqual(rows[0]["btc_price"], 60000.0)
        self.assertEqual(rows[0]["network_hashrate"], 500.0)
        stale = dict(payload)
        stale["data_quality"] = {"fields": dict(payload["data_quality"]["fields"])}
        stale["data_quality"]["fields"]["network_hashrate_eh"] = external_field(901)
        rows, _ = MODULE.observations_from_history([{"timestamp": 101, "data": stale}])
        self.assertIsNone(rows[0]["network_hashrate"])

    def test_rejects_malformed_quality_metadata(self):
        self.assertFalse(MODULE.market_ready({
            "btc_hashprice_usd_ph_day": 40,
            "gpu_l40s_usd_hour": 1.1,
            "eur_usd": 1.1,
            "data_quality": "malformed",
        }))

    def test_rejects_reference_market_data(self):
        payload = {
            "btc_hashprice_usd_ph_day": 40,
            "gpu_l40s_usd_hour": 1.1,
            "eur_usd": 1.1,
            "data_quality": {"fields": {
                "btc_hashprice_usd_ph_day": {"source": "reference", "available": True, "age_seconds": 1},
                "gpu_l40s_usd_hour": external_field(1),
                "eur_usd": external_field(1),
            }},
        }
        self.assertFalse(MODULE.market_ready(payload))

    def test_accepts_external_market_data_with_field_specific_freshness(self):
        payload = {
            "btc_hashprice_usd_ph_day": 40,
            "gpu_l40s_usd_hour": 1.1,
            "eur_usd": 1.1,
            "data_quality": {"fields": {
                "btc_hashprice_usd_ph_day": external_field(899),
                "gpu_l40s_usd_hour": external_field(899),
                "eur_usd": external_field(95 * 3600),
            }},
        }
        self.assertTrue(MODULE.market_ready(payload))

    def test_rejects_stale_or_nonfinite_inputs(self):
        payload = {
            "btc_hashprice_usd_ph_day": float("nan"),
            "gpu_l40s_usd_hour": 1.1,
            "eur_usd": 1.1,
            "data_quality": {"fields": {
                "btc_hashprice_usd_ph_day": external_field(1),
                "gpu_l40s_usd_hour": external_field(901),
                "eur_usd": external_field(1),
            }},
        }
        self.assertFalse(MODULE.market_ready(payload))

    def test_economics_preserves_negative_mining_margin_and_signed_tariff(self):
        row = {"btc": 1, "gpu": 0.1, "fx": 1.1, "gpu_power": 0.35,
               "gpu_util": 0.7, "gpu_fee": 0.15}
        costly = MODULE.economics(row, 0.30)
        credited = MODULE.economics(row, -0.05)
        self.assertLess(costly["BTC Mining"], 0)
        self.assertGreater(credited["BTC Mining"], costly["BTC Mining"])

    def test_grid_and_battery_references_are_explicit_nonnegative_values(self):
        row = {"btc": 40, "gpu": 1.1, "fx": 1.1, "gpu_power": 0.35,
               "gpu_util": 0.7, "gpu_fee": 0.15}
        values = MODULE.economics(row, 0.25)
        self.assertAlmostEqual(values["Battery"], 0.0489, places=6)
        self.assertAlmostEqual(values["Grid"], 0.055, places=6)

    def test_btc_hashprice_uses_ph_per_day_to_kwh_conversion_and_pool_fee_once(self):
        row = {"btc": 40, "gpu": 1.1, "fx": 1.1, "gpu_power": 0.35,
               "gpu_util": 0.7, "gpu_fee": 0.15}
        # 1 PH/s = 1,000 TH/s = 20 kW at 20 J/TH; 1 kW is 0.05 PH/s.
        gross_eur_per_kwh = (40 / 1.1) / (20 * 24)
        expected = 0.98 * (gross_eur_per_kwh * 0.98 - 0.25)
        actual = MODULE.economics(row, 0.25)["BTC Mining"]
        self.assertAlmostEqual(actual, expected, places=12)
        self.assertLess(actual, 0.0)

    def test_gpu_marketplace_fee_and_uptime_are_not_double_charged(self):
        row = {"btc": 40, "gpu": 1.1, "fx": 1.1, "gpu_power": 0.35,
               "gpu_util": 0.7, "gpu_fee": 0.15}
        gross = (1.1 / 1.1) * 0.7 * 0.85 / 0.35
        expected = 0.95 * (gross - 0.25)
        actual = MODULE.economics(row, 0.25, gpu_uptime=0.95)["AI Compute"]
        self.assertAlmostEqual(actual, expected, places=12)

    def test_btc_capex_hurdle_uses_same_corrected_energy_conversion(self):
        row = {"btc": 40, "gpu": 1.1, "fx": 1.1, "gpu_power": 0.35,
               "gpu_util": 0.7, "gpu_fee": 0.15}
        revenue = (40 / 1.1) / (20 * 24) * 0.98
        expected = round(max(0.0, revenue - 0.01) * 0.98 * 3.5 * 8760 * 4, 2)
        actual = MODULE.capex_hurdles(row, 0.01)["btc_capex_break_even_eur_at_4y_no_maintenance"]
        self.assertEqual(actual, expected)

    def test_production_parametric_choice_is_deterministic(self):
        row = {"ts": 100, "btc": 40, "gpu": 1.1, "fx": 1.1,
               "gpu_power": 0.35, "gpu_util": 0.7, "gpu_fee": 0.15}
        a = MODULE.production_parametric_choice(row, 0.25, 42)
        b = MODULE.production_parametric_choice(row, 0.25, 42)
        self.assertEqual(a, b)
        self.assertIn(a[0], {"AI Compute", "BTC Mining", "Battery", "Grid"})

    def test_empirical_risk_choice_is_deterministic_and_uses_only_past_rows(self):
        rows = []
        for i in range(40):
            rows.append({"ts": i * 300, "btc": 40 * (1 + 0.001 * i),
                         "gpu": 1.1 * (1 + 0.002 * i), "fx": 1.1,
                         "gpu_power": 0.35, "gpu_util": 0.7, "gpu_fee": 0.15})
        a = MODULE.empirical_risk_choice(rows[:30], rows[29], 0.25, 77)
        b = MODULE.empirical_risk_choice(rows[:30], rows[29], 0.25, 77)
        self.assertEqual(a, b)
        self.assertIsNotNone(a)

    def test_gpu_sensitivity_applies_capex_maintenance_uptime_and_lower_utilization(self):
        row = {"ts": 1, "btc": 40, "gpu": 1.1, "fx": 1.1,
               "gpu_power": 0.35, "gpu_util": 0.70, "gpu_fee": 0.15}
        free = MODULE.economics(row, 0.05)["AI Compute"]
        costed = MODULE.economics(row, 0.05, gpu_capex=10000,
                                  gpu_maintenance_eur_year=750,
                                  gpu_lifetime_years=5, gpu_uptime=0.95,
                                  gpu_utilization=0.30)["AI Compute"]
        self.assertLess(costed, free)
        self.assertLess(costed, MODULE.economics(
            row, 0.05, gpu_capex=10000, gpu_maintenance_eur_year=750,
            gpu_lifetime_years=5, gpu_uptime=0.95,
            gpu_utilization=0.70)["AI Compute"])

    def test_gpu_sensitivity_uses_matching_maintenance_assumption_key(self):
        row = {"ts": 1, "btc": 40, "gpu": 1.1, "fx": 1.1,
               "gpu_power": 0.35, "gpu_util": 0.70, "gpu_fee": 0.15}
        result = MODULE.conservative_gpu_sensitivity([row], 0.25)
        self.assertEqual(set(result), {
            "low_utilization_costed", "mid_utilization_costed", "high_utilization_costed"
        })
        for case in result.values():
            self.assertEqual(case["maintenance_eur_year"], 750.0)
            self.assertFalse(case["realized_cashflow_validated"])




    def test_btc_cost_sensitivity_reports_explicit_nonzero_cost_cases(self):
        row = {"ts": 1, "btc": 40, "gpu": 1.1, "fx": 1.1,
               "gpu_power": 0.35, "gpu_util": 0.70, "gpu_fee": 0.15}
        result = MODULE.btc_mining_sensitivity([row], 0.05)
        self.assertIn("costed_asic_5000_eur", result["cases"])
        self.assertIn("conservative_asic_8000_eur", result["cases"])
        base = result["cases"]["production_reference_inputs"]["net_contribution_eur_per_available_site_kwh"]["mean_eur_per_kwh"]
        costed = result["cases"]["costed_asic_5000_eur"]["net_contribution_eur_per_available_site_kwh"]["mean_eur_per_kwh"]
        self.assertLess(costed, base)
        self.assertFalse(result["cases"]["costed_asic_5000_eur"]["realized_cashflow_validated"])

    def test_btc_facility_overhead_efficiency_and_fixed_costs_reduce_margin(self):
        row = {"btc": 40, "gpu": 1.1, "fx": 1.1, "gpu_power": 0.35,
               "gpu_util": 0.7, "gpu_fee": 0.15}
        base = MODULE.economics(row, 0.05)["BTC Mining"]
        overhead = MODULE.economics(row, 0.05, btc_facility_overhead_kw=0.5)["BTC Mining"]
        inefficient = MODULE.economics(row, 0.05, btc_asic_efficiency_j_th=25.0)["BTC Mining"]
        costed = MODULE.economics(
            row, 0.05, btc_capex=5000, btc_maintenance_eur_year=300,
            btc_lifetime_years=4.0)["BTC Mining"]
        self.assertLess(overhead, base)
        self.assertLess(inefficient, base)
        self.assertLess(costed, base)

    def test_72h_forecast_uses_true_daily_and_weekly_elapsed_time_lags(self):
        interval = 300
        period_day = 24 * 3600 // interval
        period_week = 168 * 3600 // interval
        values = [20.0 + math.sin(2.0 * math.pi * i / period_day) for i in range(4000)]
        origin = len(values) - 1
        candidates = MODULE._horizon_candidate_values(values, origin, 72 * 3600 // interval, interval)
        self.assertAlmostEqual(candidates["mean_24"],
                               sum(values[-period_day:]) / period_day, places=10)
        self.assertAlmostEqual(candidates["seasonal_24h"], values[origin], places=10)
        self.assertAlmostEqual(
            candidates["seasonal_168h"],
            values[origin + 72 * 3600 // interval - period_week], places=10)

    def test_btc_structural_candidate_uses_external_price_and_network_series(self):
        interval = 300
        values = []
        prices = []
        network = []
        for i in range(4000):
            price = 60000.0 * math.exp(0.0001 * i + 0.01 * math.sin(i / 25.0))
            hashrate = 500.0 * math.exp(0.00001 * i)
            prices.append(price)
            network.append(hashrate)
            values.append(12.0 * price / 60000.0 / (hashrate / 500.0))
        weights = MODULE.corrected_forecast_weights(
            values, sample_steps=72 * 3600 // interval, interval_seconds=interval,
            log_scale=True, btc_price_values=prices, network_values=network)
        self.assertIn("btc_price_network_structural", weights)
        self.assertIn("btc_price_trend_network_hold", weights)
        structural = MODULE._btc_price_network_candidate(
            values, prices, network, len(values) - 1, 72 * 3600 // interval,
            interval, network_damping=0.25)
        network_hold = MODULE._btc_price_network_candidate(
            values, prices, network, len(values) - 1, 72 * 3600 // interval,
            interval, network_damping=0.0)
        self.assertNotAlmostEqual(structural, network_hold)
        prediction = MODULE.corrected_forecast_value(
            values, weights, 72, interval, log_scale=True,
            btc_price_values=prices, network_values=network)
        self.assertGreater(prediction, 0.0)

    def test_btc_log_scale_forecast_remains_positive(self):
        values = [30.0 * math.exp(0.0002 * i + 0.02 * math.sin(i / 20.0)) for i in range(4000)]
        weights = MODULE.corrected_forecast_weights(
            values, sample_steps=12, interval_seconds=300, log_scale=True)
        forecast = MODULE.corrected_forecast_value(
            values, weights, 1, 300, log_scale=True)
        self.assertGreater(forecast, 0.0)

    def test_candidate_selection_rejects_regime_shift_that_fails_untouched_holdout(self):
        baseline = [1.0] * 100
        model = [0.5] * 75 + [1.5] * 25
        result = MODULE.candidate_holdout_skill(model, baseline)
        self.assertGreaterEqual(result["training_skill"], 0.15)
        self.assertLess(result["validation_skill"], 0.05)
        self.assertFalse(result["eligible"])

    def test_time_based_seasonal_candidate_matches_daily_cycle(self):
        period = 120
        values = [10.0 + math.sin(2.0 * math.pi * i / period) for i in range(1000)]
        weights = MODULE.corrected_forecast_weights(values, sample_steps=30, interval_seconds=720)
        self.assertEqual(weights["seasonal_24h"], 1.0)
        prediction = MODULE.corrected_forecast_value(
            values, {"seasonal_24h": 1.0, **{
                name: 0.0 for name in ("recent_mean", "last_value", "trend", "mean_24",
                    "median_24", "seasonal_168h", "ewma_02", "ewma_05")
            }}, 6, 720
        )
        expected_index = len(values) - 1 + 30 - period
        self.assertAlmostEqual(prediction, values[expected_index], places=10)

    def test_corrected_forecast_guard_falls_back_to_persistence_without_five_percent_gain(self):
        flat = [10.0] * 60
        diagnostics = {}
        weights = MODULE.corrected_forecast_weights(flat, diagnostics=diagnostics)
        self.assertEqual(weights["last_value"], 1.0)
        self.assertEqual(sum(weights.values()), 1.0)
        self.assertEqual(diagnostics["rejection_reason"], "insufficient_history")
        self.assertEqual(diagnostics["candidate_count"], 0)

    def test_selector_diagnostics_explain_candidate_rejection_without_changing_forecast(self):
        values = [10.0 + 0.001 * i + 0.2 * math.sin(i / 13.0) for i in range(5000)]
        diagnostics = {}
        weights = MODULE.corrected_forecast_weights(
            values, sample_steps=12, interval_seconds=300, diagnostics=diagnostics)
        self.assertIn(diagnostics["rejection_reason"], {
            "persistence_best", "holdout_skill_below_5pct",
            "holdout_block_instability", "selected",
        })
        self.assertGreaterEqual(diagnostics["training_origins"], 32)
        self.assertGreaterEqual(diagnostics["candidate_count"], 8)
        self.assertEqual(sum(weights.values()), 1.0)
        if diagnostics["rejection_reason"] == "selected":
            self.assertNotEqual(weights["last_value"], 1.0)
        else:
            self.assertEqual(weights["last_value"], 1.0)

    def test_validator_model_selection_matches_production_holdout_gate(self):
        source = inspect.getsource(MODULE.corrected_forecast_weights)
        self.assertIn("valid_model = errors[best][split:]", source)
        self.assertIn("valid_base = errors[\"last_value\"][split:]", source)
        self.assertIn("holdout_skill < 0.05 or not stable", source)
        self.assertNotIn("ci[\"lower_95\"] <= 0.0", source)

    def test_history_parser_preserves_source_observation_timestamps(self):
        timestamp = 1_800_000_000.0
        payload = {
            "btc_hashprice_usd_ph_day": 32.0,
            "gpu_l40s_usd_hour": 1.25,
            "eur_usd": 1.10,
            "data_quality": {
                "fields": {
                    "btc_hashprice_usd_ph_day": external_field(12.0),
                    "gpu_l40s_usd_hour": external_field(24.0),
                    "eur_usd": external_field(3600.0),
                }
            },
        }
        observations, rejected = MODULE.observations_from_history([
            {"timestamp": timestamp, "data": payload}
        ])
        self.assertEqual(rejected, 0)
        self.assertEqual(observations[0]["btc_observed_at"], timestamp - 12.0)
        self.assertEqual(observations[0]["gpu_observed_at"], timestamp - 24.0)

    def test_history_parser_drops_repeated_or_out_of_order_gpu_source_observations(self):
        timestamp = 1_800_000_000.0
        raw = []
        for offset, gpu_age in ((0.0, 24.0), (720.0, 900.0)):
            payload = {
                "btc_hashprice_usd_ph_day": 32.0,
                "gpu_l40s_usd_hour": 1.25,
                "eur_usd": 1.10,
                "data_quality": {
                    "fields": {
                        "btc_hashprice_usd_ph_day": external_field(12.0),
                        "gpu_l40s_usd_hour": external_field(gpu_age),
                        "eur_usd": external_field(3600.0),
                    }
                },
            }
            raw.append({"timestamp": timestamp + offset, "data": payload})
        observations, rejected = MODULE.observations_from_history(raw)
        self.assertEqual(len(observations), 1)
        self.assertEqual(rejected, 1)

    def test_horizon_validation_excludes_reused_source_observations(self):
        rows = [
            {
                "ts": i * 300.0,
                "btc": 40.0 + 0.001 * i,
                "gpu": 1.0 + 0.0001 * i,
                "fx": 1.1,
                "gpu_power": 0.35,
                "gpu_util": 0.70,
                "gpu_fee": 0.15,
                "btc_observed_at": 1_700_000_000.0,
                "gpu_observed_at": 1_700_000_000.0,
            }
            for i in range(1300)
        ]
        report = MODULE.production_corrected_horizon_validation(
            rows, 0.05, horizons_hours=(1,))
        horizon = report["horizons"]["1"]
        self.assertTrue(horizon["source_timestamp_gate_applied"])
        self.assertGreater(horizon["stale_source_observation_pairs"], 0)
        self.assertEqual(horizon["pairs"], 0)
        self.assertFalse(horizon["release_eligible"])

    def test_independent_series_metrics_survive_the_other_series_fallback(self):
        rows = [
            {
                "ts": i * 300.0,
                "btc": 40.0 + 0.001 * i,
                "gpu": 1.0 + 0.0001 * i,
                "fx": 1.1,
                "gpu_power": 0.35,
                "gpu_util": 0.70,
                "gpu_fee": 0.15,
            }
            for i in range(1300)
        ]

        def select_one_series(values, sample_steps, interval_seconds, log_scale=False, **kwargs):
            return {"ewma_05": 1.0} if log_scale else {"last_value": 1.0}

        with patch.object(MODULE, "corrected_forecast_weights", side_effect=select_one_series):
            report = MODULE.production_corrected_horizon_validation(rows, 0.05, horizons_hours=(1,))

        horizon = report["horizons"]["1"]
        independent = horizon["per_strategy_independent_oos"]
        self.assertEqual(horizon["pairs"], 0)
        self.assertGreater(independent["BTC Mining"]["pairs"], 0)
        self.assertEqual(independent["AI Compute"]["pairs"], 0)
        self.assertFalse(horizon["release_eligible"])

    def test_history_requirement_report_uses_same_ceiling_stride_as_selector(self):
        # At a 12-minute cadence, 6h is 30 samples: ceil(30/8) = 4,
        # not floor(30/8) = 3. The report must match production's requirement.
        result = MODULE.horizon_history_requirements(6, 720)
        self.assertEqual(result["sample_steps"], 30)
        self.assertEqual(result["training_origin_stride"], 4)
        self.assertEqual(result["weekly_steps"], 840)
        self.assertEqual(result["minimum_feature_history_steps"], 240)
        self.assertEqual(result["model_history_required_observations"], 240 + 30 + 32 * 4)
        self.assertEqual(result["minimum_total_observations_for_horizon_validation"], 240 + 60 + 32 * 4)

    def test_daily_hashrate_history_is_used_only_after_full_bucket_and_within_age_limit(self):
        points = [
            {"timestamp": 0.0, "hashrate_eh": 900.0, "provider": "Mempool.space live network hashrate API"},
            {"timestamp": 86400.0, "hashrate_eh": 950.0, "provider": "Mempool.space live network hashrate API"},
        ]
        self.assertEqual(MODULE.daily_network_hashrate_at(points, 0.0), 900.0)
        self.assertEqual(MODULE.daily_network_hashrate_at(points, 80000.0), 900.0)
        self.assertEqual(MODULE.daily_network_hashrate_at(points, 86400.0), 950.0)
        self.assertEqual(MODULE.daily_network_hashrate_at(points, 180000.0), 950.0)
        untrusted = [{"timestamp": 0.0, "hashrate_eh": 999.0, "provider": "unverified"}]
        self.assertIsNone(MODULE.daily_network_hashrate_at(untrusted, 90000.0))
        self.assertIsNone(MODULE.daily_network_hashrate_at(points, 86400.0 + 86400.0 + 36.0 * 3600.0 + 1.0))

    def test_observations_backfill_daily_network_hashrate_without_lookahead(self):
        def payload(ts, history):
            fields = {
                "btc_hashprice_usd_ph_day": external_field(1),
                "gpu_l40s_usd_hour": external_field(1),
                "eur_usd": external_field(1),
                "btc_price_usd": external_field(1),
                "btc_difficulty": external_field(1),
            }
            return {"timestamp": ts, "data": {
                "btc_hashprice_usd_ph_day": 40.0, "gpu_l40s_usd_hour": 1.1,
                "eur_usd": 1.1, "btc_price_usd": 60000.0, "btc_difficulty": 100.0,
                "data_quality": {"fields": fields},
                "network_hashrate_history": history,
            }}
        history = [
            {"timestamp": 1000.0, "hashrate_eh": 900.0, "provider": "Mempool.space live network hashrate API"},
            {"timestamp": 100000.0, "hashrate_eh": 950.0, "provider": "Mempool.space live network hashrate API"},
        ]
        rows, rejected = MODULE.observations_from_history([
            payload(91000.0, history), payload(181000.0, history)])
        self.assertEqual(rejected, 0)
        self.assertEqual(rows[0]["network_hashrate"], 900.0)
        self.assertEqual(rows[0]["network_hashrate_source"], "mempool_daily_hashrate_history")
        self.assertEqual(rows[1]["network_hashrate"], 950.0)

    def test_btc_network_proxy_reports_mixed_live_and_daily_history(self):
        rows = [
            {"network_hashrate": 950.0, "network_hashrate_source": "network_hashrate"},
            {"network_hashrate": 900.0, "network_hashrate_source": "mempool_daily_hashrate_history"},
        ]
        values, source = MODULE.select_btc_network_proxy(rows)
        self.assertEqual(values, [950.0, 900.0])
        self.assertEqual(source, "mixed_live_and_daily_hashrate_history")

    def test_btc_network_proxy_uses_difficulty_only_as_explicit_fallback(self):
        rows = [{"network_hashrate": None, "btc_difficulty": 100.0 + i}
                for i in range(40)]
        values, source = MODULE.select_btc_network_proxy(rows)
        self.assertEqual(source, "btc_difficulty_proxy")
        self.assertEqual(values, [100.0 + i for i in range(40)])
        rows[0]["network_hashrate"] = 500.0
        values, source = MODULE.select_btc_network_proxy(rows)
        self.assertEqual(source, "btc_difficulty_proxy")
        complete_hashrate = [{"network_hashrate": 500.0 + i, "btc_difficulty": 100.0 + i}
                             for i in range(40)]
        values, source = MODULE.select_btc_network_proxy(complete_hashrate)
        self.assertEqual(source, "network_hashrate")

    def test_btc_selector_diagnostics_identify_difficulty_proxy_provenance(self):
        values = [10.0 + 0.001 * i + 0.2 * math.sin(i / 13.0) for i in range(5000)]
        prices = [60000.0 * math.exp(0.0001 * i + 0.01 * math.sin(i / 25.0))
                  for i in range(5000)]
        difficulty = [100.0] * 5000
        diagnostics = {}
        MODULE.corrected_forecast_weights(
            values, sample_steps=12, interval_seconds=300, log_scale=True,
            btc_price_values=prices, network_values=difficulty,
            diagnostics=diagnostics, network_source="btc_difficulty_proxy")
        self.assertEqual(diagnostics["network_proxy_source"], "btc_difficulty_proxy")

    def test_release_blockers_explain_data_and_selector_failures_without_relaxing_gate(self):
        blockers = MODULE.forecast_release_blockers(
            pairs=0, skill=None, ci={"lower_95": None}, nonnegative=False,
            persistence_fallback_pairs=190, insufficient_separated_origin_fallbacks=94)
        self.assertIn("fewer_than_100_genuine_oos_pairs", blockers)
        self.assertIn("insufficient_horizon_separated_training_history", blockers)
        self.assertIn("btc_or_gpu_selector_fell_back_to_persistence", blockers)
        self.assertIn("mae_skill_below_5pct_or_unavailable", blockers)
        self.assertIn("bootstrap_lower_95_not_positive", blockers)
        self.assertIn("at_least_one_strategy_has_negative_or_unavailable_skill", blockers)
        passing = MODULE.forecast_release_blockers(
            pairs=120, skill=0.08, ci={"lower_95": 0.01}, nonnegative=True,
            persistence_fallback_pairs=0, insufficient_separated_origin_fallbacks=0)
        self.assertEqual(passing, [])

    def test_oos_history_projection_estimates_data_lower_bound_without_changing_release_gate(self):
        result = MODULE.oos_history_projection(
            72, 722, current_observations=3183, evaluation_origin_stride=15,
            minimum_oos_pairs=100)
        requirements = MODULE.horizon_history_requirements(72, 722)
        expected_stride = math.ceil(72 * 3600 / 722)
        expected_total = (requirements["model_history_required_observations"]
                          + requirements["sample_steps"] + 99 * expected_stride)
        self.assertEqual(result["minimum_oos_pairs"], 100)
        self.assertEqual(result["evaluation_origin_stride"], expected_stride)
        self.assertTrue(result["non_overlapping_oos_windows"])
        self.assertEqual(result["minimum_total_observations_estimate"], expected_total)
        self.assertEqual(result["additional_observations_estimate"], expected_total - 3183)
        self.assertGreater(result["estimated_additional_days_at_current_cadence"], 250.0)
        self.assertTrue(result["data_only_lower_bound_not_release_prediction"])
        self.assertFalse(MODULE.horizon_release_gate({"72": {"release_eligible": False}})["status"] == "pass")

    def test_horizon_origin_stride_caps_overlapping_windows_at_eight(self):
        # A 6h horizon at a 12-minute cadence has 30 steps. Floor division
        # yielded stride 3 (nine overlapping windows); ceil yields stride 4.
        sample_steps = 30
        stride = math.ceil(sample_steps / 8)
        self.assertEqual(stride, 4)
        self.assertLessEqual(math.ceil(sample_steps / stride), 8)

    def test_validator_selector_uses_ceiling_stride_and_separate_32_origins(self):
        source = inspect.getsource(MODULE.corrected_forecast_weights)
        self.assertIn("math.ceil(sample_steps / 8)", source)
        self.assertIn("minimum_training_origins = 32", source)
        self.assertIn("split = int(count * 0.75)", source)
        self.assertIn("split < 24 or count - split < 8", source)
        production_source = (Path(__file__).resolve().parents[1] / "saas_api.py").read_text(encoding="utf-8")
        self.assertIn("origin_stride=max(1,math.ceil(sample_steps/8))", production_source)
        self.assertIn("minimum_history=minimum_feature_history_steps+sample_steps+minimum_training_origins*max(1,math.ceil(sample_steps/8))", production_source)
        self.assertIn("confidence_stride=max(1,math.ceil(sample_steps/8))", production_source)

    def test_horizon_validation_excludes_persistence_fallbacks_from_oos_pairs(self):
        source = inspect.getsource(MODULE.production_corrected_horizon_validation)
        self.assertIn("genuine_model_pairs", source)
        self.assertIn("persistence_fallback_pairs", source)
        self.assertIn("selector_rejection_fallback_pairs", source)
        self.assertIn("btc_persistence_selector_fallbacks", source)
        self.assertIn("gpu_persistence_selector_fallbacks", source)
        self.assertIn("btc_weights_are_learned and gpu_weights_are_learned", source)
        self.assertIn("pairs >= 100", source)

    def test_72h_model_selection_spaces_origins_by_horizon(self):
        source = inspect.getsource(MODULE.corrected_forecast_weights)
        self.assertIn("origin_stride = max(1, math.ceil(sample_steps / 8))", source)
        self.assertIn("minimum_training_origins = 32", source)
        self.assertIn("minimum_training_origins * origin_stride", source)
        self.assertIn("120 * origin_stride", source)

    def test_report_exposes_long_horizon_persistence_fallback_and_total_history_need(self):
        source = inspect.getsource(MODULE.production_corrected_horizon_validation)
        self.assertIn("persistence_fallback_due_insufficient_separated_origins", source)
        self.assertIn("selector_minimum_history", source)
        requirements = inspect.getsource(MODULE.horizon_history_requirements)
        self.assertIn("minimum_total_observations_for_horizon_validation", requirements)
        self.assertIn("minimum_total_observations_for_100_nonoverlapping_oos_pairs", requirements)
        self.assertIn("math.ceil(sample_steps / 8)", requirements)
        self.assertIn("last_oos_origin_ts", source)

    def test_72h_falls_back_until_32_separated_training_origins_exist(self):
        values = [100.0 + 0.001 * i for i in range(2037)]
        weights = MODULE.corrected_forecast_weights(
            values, sample_steps=360, interval_seconds=722.0, log_scale=True)
        self.assertEqual(weights["last_value"], 1.0)
        self.assertEqual(sum(weights.values()), 1.0)

    def test_training_window_includes_the_forecast_origin_observation(self):
        source = inspect.getsource(MODULE._horizon_candidate_values)
        self.assertIn("period_168h = max(1, round(168.0 * 3600.0 / interval))", source)

    def test_corrected_forecast_guard_selects_trend_only_when_holdout_proves_gain(self):
        rising = [10.0 + i * 0.1 for i in range(300)]
        weights = MODULE.corrected_forecast_weights(rising, interval_seconds=3600)
        self.assertEqual(weights["trend"], 1.0)
        self.assertEqual(sum(weights.values()), 1.0)

    def test_long_horizon_uses_persistence_when_history_is_too_short(self):
        values = [10.0 + i * 0.1 for i in range(80)]
        weights = MODULE.corrected_forecast_weights(values, sample_steps=120)
        self.assertEqual(weights["last_value"], 1.0)
        self.assertEqual(sum(weights.values()), 1.0)

    def test_candidate_selection_uses_positive_block_bootstrap_evidence(self):
        ci = MODULE._block_bootstrap_skill_ci(
            [1.0] * 100, [2.0] * 100, seed=7, block_length=12, replicates=20
        )
        self.assertEqual(ci["replicates"], 20)
        self.assertGreater(ci["lower_95"], 0.0)

    def test_candidate_must_improve_in_every_chronological_block(self):
        stable_model = [0.5] * 100
        baseline = [1.0] * 100
        unstable_model = [0.5] * 75 + [2.0] * 25
        self.assertTrue(MODULE._candidate_stable_across_blocks(stable_model, baseline, 4))
        self.assertFalse(MODULE._candidate_stable_across_blocks(unstable_model, baseline, 4))

    def test_horizon_release_gate_requires_every_advertised_horizon(self):
        passed = {"release_eligible": True}
        failed = {"release_eligible": False}
        gate = MODULE.horizon_release_gate({
            "1": passed, "6": passed, "24": passed, "72": failed
        })
        self.assertEqual(gate["status"], "blocked")
        self.assertEqual(gate["ineligible_horizons_hours"], [72])
        complete = MODULE.horizon_release_gate({
            "1": passed, "6": passed, "24": passed, "72": passed
        })
        self.assertEqual(complete["status"], "pass")
        missing = MODULE.horizon_release_gate({
            "1": passed, "6": passed, "24": passed
        })
        self.assertEqual(missing["ineligible_horizons_hours"], [72])

    def test_corrected_horizon_validation_covers_api_forecast_horizons(self):
        rows = []
        for i in range(650):
            rows.append({
                "ts": i * 300,
                "btc": 35.0 + 0.002 * i + 0.15 * math.sin(i / 9.0),
                "gpu": 1.0 + 0.0008 * i + 0.02 * math.sin(i / 13.0),
                "fx": 1.10 + 0.00001 * i,
                "gpu_power": 0.35, "gpu_util": 0.70, "gpu_fee": 0.15,
            })
        result = MODULE.production_corrected_horizon_validation(rows, 0.20)
        self.assertEqual(set(result["horizons"]), {"1", "6", "24", "72"})
        for horizon in ("1", "6", "24", "72"):
            self.assertEqual(result["horizons"][horizon]["pairs"], 0)
        self.assertFalse(result["release_eligible"])
        self.assertFalse(result["oos_evaluation_policy"]["overlapping_oos_windows"])
        self.assertEqual(result["oos_evaluation_policy"]["bootstrap_block_length_horizon_windows"], 6)
        self.assertEqual(result["horizons"]["1"]["bootstrap_block_length"], 6)
        self.assertEqual(result["horizons"]["24"]["bootstrap_block_length"], 6)
        self.assertTrue(result["horizons"]["72"]["oos_windows_non_overlapping"])
        for horizon in result["horizons"].values():
            self.assertIn("mae_skill_vs_persistence", horizon)
            self.assertIn("mae_skill_confidence_interval_95", horizon)

    def test_outer_oos_origins_are_separated_by_at_least_the_forecast_horizon(self):
        rows = [
            {"ts": i * 300, "btc": 40.0 + 0.01 * i, "gpu": 1.0 + 0.001 * i,
             "fx": 1.1, "gpu_power": 0.35, "gpu_util": 0.7, "gpu_fee": 0.15}
            for i in range(200)
        ]
        result = MODULE.production_corrected_horizon_validation(rows, 0.20)
        one_hour = result["horizons"]["1"]
        # 200 five-minute observations produce 14 valid, non-overlapping 1h
        # windows after the validator's initial 23-row warm-up. Models lack
        # the required weekly training history, so all remain fallbacks.
        self.assertEqual(one_hour["total_horizon_matched_origins"], 14)
        self.assertEqual(one_hour["persistence_fallback_pairs"], 14)
        self.assertEqual(one_hour["pairs"], 0)
        self.assertTrue(one_hour["oos_windows_non_overlapping"])
        self.assertEqual(one_hour["history_growth_projection"]["oos_pair_spacing_hours"], 1.0)

    def test_forecast_quality_gate_blocks_persistence_only_calibration(self):
        rows = []
        for i in range(38):
            rows.append({"ts": i * 300, "btc": 40 * (1 + 0.001 * i),
                         "gpu": 1.1 * (1 + 0.002 * i), "fx": 1.1,
                         "gpu_power": 0.35, "gpu_util": 0.7, "gpu_fee": 0.15})
        result = MODULE.run_backtest(rows, 0.25)
        self.assertEqual(result["forecast_quality_gate"]["status"], "blocked")
        self.assertEqual(result["forecast_quality_gate"]["minimum_mae_skill_vs_persistence"], 0.05)

    def test_calibration_weight_uses_prior_errors_and_is_bounded(self):
        self.assertEqual(MODULE.calibrated_shrinkage_weight([(1.0, 1.0)] * 23), 0.0)
        self.assertGreater(MODULE.calibrated_shrinkage_weight([(1.0, 1.0)] * 24), 0.8)
        self.assertEqual(MODULE.calibrated_shrinkage_weight([(1.0, -1.0)] * 24), 0.0)
        self.assertEqual(MODULE.calibrated_shrinkage_weight([(0.0, 1.0)] * 24), 0.0)

    def test_walk_forward_calibration_reports_weights_and_baseline(self):
        rows = []
        for i in range(38):
            rows.append({"ts": i * 300, "btc": 40 * (1 + 0.001 * i),
                         "gpu": 1.1 * (1 + 0.002 * i), "fx": 1.1,
                         "gpu_power": 0.35, "gpu_util": 0.7, "gpu_fee": 0.15})
        result = MODULE.run_backtest(rows, 0.25)
        calibrated = result["walk_forward_shrunk_forecast"]
        self.assertEqual(calibrated["pairs"], result["independent_empirical_paired_bootstrap"]["pairs"])
        self.assertIn("mae_skill_vs_persistence", calibrated)
        self.assertEqual(result["forecast_calibration"]["minimum_prior_pairs_before_nonzero_correction"], 24)
        self.assertTrue(all(0.0 <= value <= 1.0 for value in
                            result["forecast_calibration"]["mean_correction_weight_by_strategy"].values()))

    def test_backtest_reports_persistence_skill_and_risk_sensitivity(self):
        pairs = [{
            "chosen": "AI Compute", "predicted": 0.12, "baseline_predicted": 0.10,
            "predicted_win_probability": 0.8, "actual": 0.11, "best": "AI Compute",
            "regret": 0.0, "actual_values": {
                "AI Compute": 0.11, "BTC Mining": 0.02, "Battery": 0.04, "Grid": 0.03
            }, "gap_minutes": 5.0,
        }]
        result = MODULE.summarize_backtest(pairs, "unit-test")
        self.assertAlmostEqual(result["mean_prediction_error_eur_per_kwh"], 0.01)
        self.assertAlmostEqual(result["persistence_baseline_mae_eur_per_kwh"], 0.01)
        self.assertAlmostEqual(result["mae_skill_vs_persistence"], 0.0)
        self.assertAlmostEqual(result["chosen_strategy_win_probability_brier"], 0.04)

    def test_regret_evidence_is_never_marked_validated_from_market_proxy_pairs(self):
        def pair(chosen, best):
            return {
                "chosen": chosen, "predicted": 0.12, "baseline_predicted": 0.10,
                "predicted_win_probability": 0.8, "actual": 0.11, "best": best,
                "regret": 0.0, "actual_values": {
                    "AI Compute": 0.11, "BTC Mining": 0.02,
                    "Battery": 0.04, "Grid": 0.03,
                }, "gap_minutes": 5.0,
            }

        dominant = MODULE.summarize_backtest([pair("AI Compute", "AI Compute")] * 5, "dominant")
        self.assertEqual(dominant["regret_evidence_status"], "low_information_single_strategy_dominance")
        self.assertFalse(dominant["regret_evidence_validated"])

        varied = MODULE.summarize_backtest([
            pair("AI Compute", "AI Compute"), pair("BTC Mining", "BTC Mining")
        ], "varied")
        self.assertEqual(varied["regret_evidence_status"], "historical_market_proxy_not_realized_farm_decisions")
        self.assertFalse(varied["regret_evidence_validated"])

        empty = MODULE.summarize_backtest([], "empty")
        self.assertEqual(empty["regret_evidence_status"], "insufficient_data_not_validated")
        self.assertFalse(empty["regret_evidence_validated"])

    def test_backtest_excludes_noncomparable_long_snapshot_gaps(self):
        rows = []
        for i in range(28):
            rows.append({"ts": i * (7200 if i == 27 else 300), "btc": 40 + i * 0.01,
                         "gpu": 1.1 + i * 0.001, "fx": 1.1,
                         "gpu_power": 0.35, "gpu_util": 0.7, "gpu_fee": 0.15})
        result = MODULE.run_backtest(rows, 0.25)
        self.assertEqual(result["excluded_long_gap_pairs"], 1)
        self.assertEqual(result["maximum_accepted_pair_gap_minutes"], 60.0)

    def test_risk_aversion_sensitivity_reports_each_candidate_without_mutating_default(self):
        rows = []
        for i in range(36):
            rows.append({"ts": i * 300, "btc": 40 * (1 + 0.001 * i),
                         "gpu": 1.1 * (1 + 0.002 * i), "fx": 1.1,
                         "gpu_power": 0.35, "gpu_util": 0.7, "gpu_fee": 0.15})
        result = MODULE.run_backtest(rows, 0.25)
        sensitivity = result["risk_aversion_sensitivity"]
        self.assertEqual(sensitivity["current_production_alpha"], MODULE.RISK_AVERSION)
        self.assertEqual(set(sensitivity["results"]), {"0.0", "0.25", "0.5", "0.75", "1.0"})
        self.assertTrue(all("mean_regret_eur_per_kwh" in value
                            for value in sensitivity["results"].values()))

    def test_investment_readiness_fails_closed_without_realized_cashflow(self):
        gate = MODULE.investment_readiness()
        self.assertEqual(gate["status"], "not_investment_validated")
        self.assertFalse(gate["realized_cashflow_validated"])
        self.assertFalse(gate["confidence_calibrated"])
        self.assertTrue(gate["zero_cost_defaults_are_not_investment_inputs"])


    def test_capex_hurdles_are_zero_when_strategy_has_no_positive_contribution(self):
        row = {"btc": 0.01, "gpu": 0.01, "fx": 1.1, "gpu_power": 0.35,
               "gpu_util": 0.7, "gpu_fee": 0.15}
        result = MODULE.capex_hurdles(row, 0.30)
        self.assertEqual(result["gpu_capex_break_even_eur_at_5y_no_maintenance"], 0)
        self.assertEqual(result["btc_capex_break_even_eur_at_4y_no_maintenance"], 0)


if __name__ == "__main__":
    unittest.main()
