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

    def test_capex_hurdles_are_zero_when_strategy_has_no_positive_contribution(self):
        row = {"btc": 0.01, "gpu": 0.01, "fx": 1.1, "gpu_power": 0.35,
               "gpu_util": 0.7, "gpu_fee": 0.15}
        result = MODULE.capex_hurdles(row, 0.30)
        self.assertEqual(result["gpu_capex_break_even_eur_at_5y_no_maintenance"], 0)
        self.assertEqual(result["btc_capex_break_even_eur_at_4y_no_maintenance"], 0)


if __name__ == "__main__":
    unittest.main()
