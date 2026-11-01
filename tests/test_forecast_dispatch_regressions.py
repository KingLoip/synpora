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

    def test_adaptive_weights_use_rolling_origin_errors_on_inference_window(self):
        source = ast.get_source_segment(SOURCE, find_function("_adaptive_model_weights"))
        self.assertIn("history=y[max(0,target_idx-48):target_idx]", source)
        self.assertIn("first=max(8,len(y)-48)", source)
        self.assertIn('"last_value":last', source)
        self.assertIn("len(y)<24", source)

    def test_model_selection_uses_the_same_rolling_origin_window(self):
        source = ast.get_source_segment(SOURCE, find_function("_model_select"))
        self.assertIn("history=y[max(0,target_idx-48):target_idx]", source)
        self.assertIn("first=max(8,len(y)-48)", source)

    def test_ensemble_forecast_converts_hours_to_snapshot_steps_and_damps_trend(self):
        source = ast.get_source_segment(SOURCE, find_function("_ensemble_forecast"))
        self.assertIn("last=y[-1]", source)
        self.assertIn("sample_steps=(h*3600.0)/interval_seconds", source)
        self.assertIn("math.exp(-max(0.0,sample_steps-12.0)/48.0)", source)
        self.assertIn("max(0.0,pred)", source)


class DispatchSafetyRegressionTests(unittest.TestCase):
    def test_forecast_plan_marks_input_fallback_as_unverified(self):
        source = ast.get_source_segment(SOURCE, find_function("forecast_plan"))
        self.assertIn('_market_snapshot_freshness(c)', source)
        self.assertIn('_eligible_market_rows(c,8)', source)
        self.assertIn('forecast_method="user_input_fallback_unverified"', source)
        self.assertIn('"ensemble_confidence":conf', source)
        self.assertIn('"pv_profile_method":"flat_allocation_of_user_supplied_horizon_total"', source)

    def test_dispatch_requires_fresh_provenance_verified_market_data(self):
        source = ast.get_source_segment(SOURCE, find_function("dispatch_plan"))
        self.assertIn('_market_snapshot_freshness(c)', source)
        self.assertIn('_eligible_market_rows(c,8)', source)
        self.assertIn('forecast_source="input_fallback_unverified"', source)
        self.assertIn('else "degraded"', source)

    def test_dispatch_does_not_invent_hourly_solar_curve(self):
        source = ast.get_source_segment(SOURCE, find_function("dispatch_plan"))
        self.assertIn("pv=[pv_total/hours]*hours", source)
        self.assertNotIn("math.sin", source)

    def test_battery_charges_only_when_future_net_value_covers_losses(self):
        source = ast.get_source_segment(SOURCE, find_function("dispatch_plan"))
        self.assertIn("latest_eligible and future*round_trip-degradation>current_best", source)

    def test_battery_discharges_only_when_current_beats_future(self):
        source = ast.get_source_segment(SOURCE, find_function("dispatch_plan"))
        self.assertIn("latest_eligible and charge<=1e-9", source)
        self.assertIn("current_best>future+degradation", source)
        self.assertNotIn("future>max(btc,gpu,grid)", source)

    def test_dispatch_remains_recommendation_only(self):
        source = ast.get_source_segment(SOURCE, find_function("dispatch_plan"))
        self.assertIn('"recommendation_only":True', source)
        self.assertIn('"hardware_write":False', source)


if __name__ == "__main__":
    unittest.main()
