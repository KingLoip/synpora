import importlib.util
import math
import unittest
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "economic_validation", Path(__file__).resolve().parents[1] / "scripts" / "economic_validation.py"
)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def external_field(age):
    return {"source": "external", "available": True, "age_seconds": age}


class EconomicValidationTests(unittest.TestCase):
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
