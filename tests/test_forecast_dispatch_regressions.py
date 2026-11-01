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

    def test_market_price_forecasts_do_not_require_site_tariff_history(self):
        source = ast.get_source_segment(SOURCE, find_function("_eligible_market_rows"))
        self.assertIn("require_site_tariff=True", source)
        self.assertIn("_snapshot_is_market_ready(payload)", source)
        self.assertIn("_snapshot_is_learning_eligible(payload)", source)

    def test_market_history_supports_compact_extended_provenance_data(self):
        source = ast.get_source_segment(SOURCE, find_function("market_history"))
        self.assertIn("compact:bool=False", source)
        self.assertIn("2000 if compact else 500", source)
        self.assertIn('"data_quality":{"fields":', source)

    def test_adaptive_weights_use_rolling_origin_errors_on_inference_window(self):
        source = ast.get_source_segment(SOURCE, find_function("_adaptive_model_weights"))
        self.assertIn("history=y[max(0,target_idx-48):target_idx]", source)
        self.assertIn("first=max(8,len(y)-48)", source)
        self.assertIn('"last_value":last', source)
        self.assertIn("len(y)<24", source)
        self.assertIn('maes[best]>=maes["last_value"]*0.95', source)
        self.assertIn('best="last_value"', source)

    def test_model_selection_uses_the_same_rolling_origin_window(self):
        source = ast.get_source_segment(SOURCE, find_function("_model_select"))
        self.assertIn("history=y[max(0,target_idx-48):target_idx]", source)
        self.assertIn("first=max(8,len(y)-48)", source)

    def test_horizon_weights_are_selected_from_same_horizon_outcomes(self):
        source = ast.get_source_segment(SOURCE, find_function("_adaptive_horizon_weights"))
        self.assertIn("values[origin+sample_steps]", source)
        self.assertIn("len(values) < sample_steps + 32", source)
        self.assertIn('maes[best]>=maes["last_value"]*0.95', source)

    def test_ensemble_forecast_uses_horizon_specific_weights_and_snapshot_cadence(self):
        source = ast.get_source_segment(SOURCE, find_function("_ensemble_forecast"))
        self.assertIn("_eligible_market_rows(c,2000,include_ts=True,require_site_tariff=False)", source)
        self.assertIn("sample_steps=max(1,round((h*3600.0)/interval_seconds))", source)
        self.assertIn("_adaptive_horizon_weights(y,sample_steps)", source)
        self.assertIn('"weights_by_horizon":horizon_weights', source)
        self.assertIn("max(0.0,pred)", source)


class DispatchSafetyRegressionTests(unittest.TestCase):
    def test_forecast_plan_marks_input_fallback_as_unverified(self):
        source = ast.get_source_segment(SOURCE, find_function("forecast_plan"))
        self.assertIn('_market_snapshot_freshness(c)', source)
        self.assertIn('_eligible_market_rows(c,24,require_site_tariff=False)', source)
        self.assertIn('forecast_method="user_input_fallback_unverified"', source)
        self.assertIn('"ensemble_confidence":conf', source)
        self.assertIn('"pv_profile_method":"flat_allocation_of_user_supplied_horizon_total"', source)

    def test_dispatch_requires_fresh_provenance_verified_market_data(self):
        source = ast.get_source_segment(SOURCE, find_function("dispatch_plan"))
        self.assertIn('_market_snapshot_freshness(c)', source)
        self.assertIn('_eligible_market_rows(c,24,require_site_tariff=False)', source)
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
