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


class ChronologicalForecastRegressionTests(unittest.TestCase):
    def test_latest_window_is_restored_to_chronological_order(self):
        source = ast.get_source_segment(SOURCE, find_function("_eligible_market_rows"))
        self.assertIn("ORDER BY ts DESC LIMIT ?", source)
        self.assertIn("rows.reverse()", source)

    def test_adaptive_weights_train_on_past_and_score_future(self):
        source = ast.get_source_segment(SOURCE, find_function("_adaptive_model_weights"))
        self.assertIn("train=y[:-cut]", source)
        self.assertIn("test=y[-cut:]", source)
        self.assertIn("last=train[-1]", source)
        self.assertIn("last+slope*(i+1)", source)

    def test_model_selection_uses_same_forward_holdout(self):
        source = ast.get_source_segment(SOURCE, find_function("_model_select"))
        self.assertIn("train=y[:-cut]", source)
        self.assertIn("test=y[-cut:]", source)
        self.assertIn("last=train[-1]", source)

    def test_ensemble_forecast_starts_from_latest_observation(self):
        source = ast.get_source_segment(SOURCE, find_function("_ensemble_forecast"))
        self.assertIn("last=y[-1]", source)
        self.assertIn("slope=(y[-1]-y[0])", source)


class DispatchSafetyRegressionTests(unittest.TestCase):
    def test_dispatch_requires_fresh_provenance_verified_market_data(self):
        source = ast.get_source_segment(SOURCE, find_function("dispatch_plan"))
        self.assertIn('_market_snapshot_freshness(c)', source)
        self.assertIn('_eligible_market_rows(c,1)', source)
        self.assertIn('forecast_source="input_fallback_unverified"', source)
        self.assertIn('else "degraded"', source)

    def test_dispatch_does_not_invent_hourly_solar_curve(self):
        source = ast.get_source_segment(SOURCE, find_function("dispatch_plan"))
        self.assertIn("pv=[pv_total/hours]*hours", source)
        self.assertNotIn("math.sin", source)

    def test_battery_charges_only_when_future_net_value_covers_losses(self):
        source = ast.get_source_segment(SOURCE, find_function("dispatch_plan"))
        self.assertIn("future*round_trip-degradation>current_best", source)

    def test_battery_discharges_only_when_current_beats_future(self):
        source = ast.get_source_segment(SOURCE, find_function("dispatch_plan"))
        self.assertIn("current_best>future+degradation", source)
        self.assertNotIn("future>max(btc,gpu,grid)", source)

    def test_dispatch_remains_recommendation_only(self):
        source = ast.get_source_segment(SOURCE, find_function("dispatch_plan"))
        self.assertIn('"recommendation_only":True', source)
        self.assertIn('"hardware_write":False', source)


if __name__ == "__main__":
    unittest.main()
