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
        # 20 J/TH means 20 W per TH/s; one kW is 50 TH/s = 0.00005 PH/s.
        gross_eur_per_kwh = (40 / 1.1) / (20 * 1000 * 24)
        expected = gross_eur_per_kwh * 0.98 - 0.25
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
        revenue = (40 / 1.1) / (20 * 1000 * 24) * 0.98
        expected = round(max(0.0, revenue - 0.25) * 3.5 * 8760 * 4, 2)
        actual = MODULE.capex_hurdles(row, 0.25)["btc_capex_break_even_eur_at_4y_no_maintenance"]
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
