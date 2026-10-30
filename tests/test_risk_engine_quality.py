import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "saas_api.py").read_text(encoding="utf-8")
TREE = ast.parse(SOURCE)


def find_function(name):
    return next(node for node in ast.walk(TREE)
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name == name)


class ForecastQualityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        node = find_function("_forecast_quality")
        module = ast.Module(body=[node], type_ignores=[])
        namespace = {}
        exec(compile(module, "saas_api.py", "exec"), namespace)
        cls.forecast_quality = staticmethod(namespace["_forecast_quality"])

    def _evaluate(self, tariff):
        rows = [{
            "btc_hashprice_usd_ph_day": 40.0,
            "gpu_hourly_usd": 1.10,
            "forecast_energy_cost_eur_kwh": tariff,
        }]
        models = {name: {"ready": True} for name in ("btc", "gpu", "energy")}
        return self.forecast_quality(rows, models, {"btc": 0.8}, 24)

    def test_negative_site_tariff_is_valid(self):
        result = self._evaluate(-0.05)
        self.assertTrue(result["valid"])
        self.assertEqual(result["status"], "validated")
        self.assertFalse(any("negative" in warning.lower() for warning in result["warnings"]))

    def test_positive_site_tariff_is_valid(self):
        result = self._evaluate(0.25)
        self.assertTrue(result["valid"])
        self.assertEqual(result["status"], "validated")

    def test_tariff_below_supported_minimum_is_invalid(self):
        result = self._evaluate(-1.01)
        self.assertFalse(result["valid"])
        self.assertEqual(result["status"], "invalid")
        self.assertTrue(any("outside the supported range" in warning for warning in result["warnings"]))

    def test_tariff_above_supported_maximum_is_invalid(self):
        result = self._evaluate(10.01)
        self.assertFalse(result["valid"])
        self.assertEqual(result["status"], "invalid")

    def test_negative_market_revenue_is_still_invalid(self):
        node = find_function("_forecast_quality")
        namespace = {}
        exec(compile(ast.Module(body=[node], type_ignores=[]), "saas_api.py", "exec"), namespace)
        rows = [{"btc_hashprice_usd_ph_day": -1.0, "gpu_hourly_usd": 1.0,
                 "forecast_energy_cost_eur_kwh": -0.05}]
        models = {name: {"ready": True} for name in ("btc", "gpu", "energy")}
        result = namespace["_forecast_quality"](rows, models, {"btc": 0.8}, 24)
        self.assertFalse(result["valid"])
        self.assertEqual(result["status"], "invalid")


class BenchmarkWindowTests(unittest.TestCase):
    def test_benchmark_selects_latest_window_then_restores_chronological_order(self):
        node = find_function("benchmark")
        source = ast.get_source_segment(SOURCE, node)
        self.assertIn("ORDER BY ts DESC, id DESC LIMIT ?", source)
        self.assertIn("rows.reverse()", source)


if __name__ == "__main__":
    unittest.main()
