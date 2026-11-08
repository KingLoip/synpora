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
        self.assertIn("10000 if compact else 500", source)
        self.assertIn('compact_payload["data_quality"]', source)
        self.assertIn('compact_payload["network_hashrate_history"]', source)
        self.assertIn('compact_payload["network_hashrate_history_source"]', source)
        self.assertIn("history_included=False", source)
        self.assertIn("if candidate_history and not history_included", source)

    def test_production_selector_uses_btc_price_trend_with_network_hold_candidate(self):
        selector = ast.get_source_segment(SOURCE, find_function("_adaptive_horizon_weights"))
        forecast = ast.get_source_segment(SOURCE, find_function("_ensemble_forecast"))
        confidence = ast.get_source_segment(SOURCE, find_function("_ensemble_confidence"))
        for source in (selector, forecast, confidence):
            self.assertIn("btc_price_trend_network_hold", source)
            self.assertIn("network_damping=0.0", source)
        structural = ast.get_source_segment(SOURCE, find_function("_btc_price_network_candidate"))
        self.assertIn("network_damped_hours", structural)
        self.assertIn("price_damped_hours", structural)

    def test_btc_network_data_provider_is_visible_in_market_provenance(self):
        live = ast.get_source_segment(SOURCE, find_function("live_market"))
        collect = ast.get_source_segment(SOURCE, find_function("collect_market"))
        for field in ("btc_price_usd", "btc_hashprice_usd_ph_day", "btc_difficulty", "network_hashrate_eh"):
            self.assertIn(field, live)
            self.assertIn(field, collect)

    def test_mempool_live_hashrate_adapter_validates_units_and_provenance(self):
        node = find_function("_mempool_network_observation")
        source = ast.get_source_segment(SOURCE, node)
        self.assertIn('raw.get("currentHashrate")', source)
        self.assertIn("hashrate_hs / 1e18", source)
        self.assertIn('raw.get("currentDifficulty")', source)
        self.assertIn('"observed_at": float(now)', source)
        adapter = ast.get_source_segment(SOURCE, find_function("_fetch_builtin_market_sources"))
        self.assertIn("https://mempool.space/api/v1/mining/hashrate/1m", adapter)
        self.assertIn('ttl_seconds=300, timeout=3, attempts=1', adapter)
        self.assertIn('_provider_json_cache_at.get(mempool_url, now)', adapter)
        self.assertIn('"Mempool.space live network hashrate API"', source)
        self.assertIn('raw.get("hashrates",[])', source)
        adapter = ast.get_source_segment(SOURCE, find_function("_fetch_builtin_market_sources"))
        self.assertIn('out["networkHashrateHistory"]=history', adapter)
        collector = ast.get_source_segment(SOURCE, find_function("collect_market"))
        self.assertIn('"network_hashrate_history":ext.get("networkHashrateHistory",[])', collector)
        rows_source = ast.get_source_segment(SOURCE, find_function("_eligible_market_rows"))
        self.assertIn("_daily_network_hashrate_at(history_points,float(row[0]))", rows_source)
        isolated = ast.Module(body=[node], type_ignores=[])
        namespace = {}
        exec(compile(ast.fix_missing_locations(isolated), "<mempool-adapter-test>", "exec"), namespace)
        observe = namespace["_mempool_network_observation"]
        result = observe({
            "currentHashrate": 1.02e21, "currentDifficulty": 148e12,
            "hashrates": [
                {"timestamp": 1791504000, "avgHashrate": 9.0e20},
                {"timestamp": 1791590400, "avgHashrate": 9.5e20},
            ],
        }, 1794000000.0)
        self.assertAlmostEqual(result["networkHashrate"]["value"], 1020.0)
        self.assertEqual(result["difficulty"]["value"], 148e12)
        self.assertEqual(result["networkHashrate"]["observed_at"], 1794000000.0)
        self.assertEqual(result["networkHashrateHistory"], [
            {"timestamp": 1791504000.0, "hashrate_eh": 900.0, "provider": "Mempool.space live network hashrate API"},
            {"timestamp": 1791590400.0, "hashrate_eh": 950.0, "provider": "Mempool.space live network hashrate API"},
        ])
        self.assertEqual(observe({"currentHashrate": -1, "currentDifficulty": "bad"}, 1.0), {})

    def test_daily_hashrate_alignment_is_time_safe_and_keeps_live_source_separate(self):
        node = find_function("_daily_network_hashrate_at")
        isolated = ast.Module(body=[node], type_ignores=[])
        namespace = {}
        exec(compile(ast.fix_missing_locations(isolated), "<daily-hashrate-alignment-test>", "exec"), namespace)
        lookup = namespace["_daily_network_hashrate_at"]
        points = [
            {"timestamp": 0.0, "hashrate_eh": 900.0, "provider": "Mempool.space live network hashrate API"},
            {"timestamp": 86400.0, "hashrate_eh": 950.0, "provider": "Mempool.space live network hashrate API"},
        ]
        self.assertIsNone(lookup(points, -1.0))
        self.assertEqual(lookup(points, 0.0), 900.0)
        self.assertEqual(lookup(points, 80000.0), 900.0)
        self.assertEqual(lookup(points, 86400.0), 950.0)
        self.assertEqual(lookup(points, 180000.0), 950.0)
        self.assertIsNone(lookup(points, 86400.0 + 86400.0 + 36.0 * 3600.0 + 1.0))

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

    def test_horizon_seasonality_uses_elapsed_hours_not_snapshot_count(self):
        source = ast.get_source_segment(SOURCE, find_function("_horizon_candidate_values"))
        self.assertIn("24.0*3600.0/interval", source)
        self.assertIn("168.0*3600.0/interval", source)
        self.assertIn('"seasonal_24h":seasonal(period_24h)', source)
        self.assertIn('"seasonal_168h":seasonal(period_168h)', source)

    def test_horizon_candidate_selection_has_a_separate_holdout(self):
        source = ast.get_source_segment(SOURCE, find_function("_adaptive_horizon_weights"))
        self.assertIn("split=int(count*0.75)", source)
        self.assertIn("skill<0.05", source)
        self.assertIn("origin_stride=max(1,math.ceil(sample_steps/8))", source)
        self.assertIn("minimum_training_origins=32", source)
        self.assertIn("minimum_training_origins*origin_stride", source)
        self.assertIn("stable", source)

    def test_production_horizon_training_includes_the_current_origin(self):
        source = ast.get_source_segment(SOURCE, find_function("_adaptive_horizon_weights"))
        self.assertIn("origin-lookback+1):origin+1", ast.get_source_segment(SOURCE, find_function("_horizon_candidate_values")))
        confidence = ast.get_source_segment(SOURCE, find_function("_ensemble_confidence"))
        self.assertIn("training=y[:origin+1]", confidence)

    def test_horizon_weights_are_selected_from_same_horizon_outcomes(self):
        source = ast.get_source_segment(SOURCE, find_function("_adaptive_horizon_weights"))
        self.assertIn("values[origin+sample_steps]", source)
        self.assertIn("minimum_training_origins=32", source)
        self.assertIn("minimum_training_origins*origin_stride", source)
        self.assertIn("skill<0.05", source)

    def test_forecast_candidate_family_includes_seasonal_and_ewma_models(self):
        source = ast.get_source_segment(SOURCE, find_function("_horizon_candidate_values"))
        for name in ("seasonal_24h", "seasonal_168h", "ewma_02", "ewma_05", "median_24"):
            self.assertIn(name, source)

    def test_ensemble_forecast_uses_horizon_specific_weights_and_snapshot_cadence(self):
        source = ast.get_source_segment(SOURCE, find_function("_ensemble_forecast"))
        self.assertIn("_eligible_market_rows(c,10000,include_ts=True,require_site_tariff=False)", source)
        self.assertIn("sample_steps=max(1,round((h*3600.0)/interval_seconds))", source)
        self.assertIn("_adaptive_horizon_weights(y,sample_steps,interval_seconds,log_scale=log_scale,", source)
        self.assertIn('"weights_by_horizon":weights_by_horizon', source)
        self.assertIn("max(0.0,pred)", source)


class DispatchSafetyRegressionTests(unittest.TestCase):
    def test_forecast_history_uses_elapsed_time_windows_and_long_history(self):
        features = ast.get_source_segment(SOURCE, find_function("_horizon_candidate_values"))
        forecast = ast.get_source_segment(SOURCE, find_function("_ensemble_forecast"))
        self.assertIn("168.0*3600.0/interval", features)
        self.assertIn("24.0*3600.0/interval", features)
        self.assertIn("_eligible_market_rows(c,10000", forecast)
        self.assertIn("series_key==\"btc\"", forecast)

    def test_btc_cost_amortization_includes_facility_overhead(self):
        source = ast.get_source_segment(SOURCE, find_function("_economics"))
        self.assertIn("(btc_device_power+btc_overhead)*8760", source)

    def test_market_history_preserves_provenance_for_btc_exogenous_inputs(self):
        source = ast.get_source_segment(SOURCE, find_function("market_history"))
        self.assertIn('"btc_price_usd","btc_difficulty","network_hashrate_eh"', source)
        self.assertIn("required+optional", source)
        eligible = ast.get_source_segment(SOURCE, find_function("_eligible_market_rows"))
        self.assertIn('_optional_external_market_value(payload,"btc_price_usd")', eligible)
        forecast = ast.get_source_segment(SOURCE, find_function("_ensemble_forecast"))
        self.assertIn("btc_price_network_structural", forecast)
        self.assertIn("btc_exogenous_fields_used", forecast)

    def test_btc_mining_diagnostics_expose_break_even_and_unverified_settlement(self):
        source = ast.get_source_segment(SOURCE, find_function("_btc_mining_metrics"))
        self.assertIn("break_even_energy_cost_eur_kwh", source)
        self.assertIn("break_even_hashprice_usd_ph_day", source)
        self.assertIn('"realized_pool_payout_verified":False', source)
        risk = ast.get_source_segment(SOURCE, find_function("risk_analysis"))
        decision = ast.get_source_segment(SOURCE, find_function("decision_engine"))
        self.assertIn('"btc_mining_analysis":_btc_mining_metrics(x)', risk)
        self.assertIn('"btc_mining_analysis":_btc_mining_metrics(x)', decision)

    def test_portfolio_btc_allocation_accounts_for_site_overhead_uptime_and_fixed_costs(self):
        source = ast.get_source_segment(SOURCE, find_function("portfolio_optimize"))
        for term in ("btc_facility_overhead_kw", "btc_uptime", "pool_fee",
                     "btc_capex_eur", "energy_cost_factor", "fixed_cost_eur_kwh"):
            self.assertIn(term, source)

    def test_forecast_plan_marks_input_fallback_as_unverified(self):
        source = ast.get_source_segment(SOURCE, find_function("forecast_plan"))
        self.assertIn('_market_snapshot_freshness(c)', source)
        self.assertIn('_eligible_market_rows(c,24,require_site_tariff=False)', source)
        self.assertIn('forecast_method="user_input_fallback_unverified"', source)
        self.assertIn('"ensemble_confidence":conf', source)
        self.assertIn('"pv_profile_method":"flat_allocation_of_user_supplied_horizon_total"', source)

    def test_forecast_confidence_is_horizon_specific_and_not_called_probability(self):
        source = ast.get_source_segment(SOURCE, find_function("_ensemble_confidence"))
        self.assertIn("horizon_hours=1", source)
        self.assertIn("sample_steps=max(1,round((float(horizon_hours)*3600.0)/interval_seconds))", source)
        self.assertIn("first_origin=len(y)-sample_steps-24*confidence_stride", source)
        self.assertIn("confidence_stride=max(1,math.ceil(sample_steps/8))", source)
        plan = ast.get_source_segment(SOURCE, find_function("forecast_plan"))
        self.assertIn('_ensemble_confidence(c,"btc",hours)', plan)
        self.assertIn('"confidence_is_probability"]=False', plan)

    def test_energy_tariff_is_treated_as_a_user_supplied_constant_not_a_market_forecast(self):
        source = ast.get_source_segment(SOURCE, find_function("forecast_plan"))
        self.assertIn('"model":"user_supplied_constant"', source)
        self.assertIn('"forecasted":False', source)
        self.assertIn('"energy":1.0', source)

    def test_dispatch_degrades_when_any_horizon_lacks_training_history(self):
        source = ast.get_source_segment(SOURCE, find_function("dispatch_plan"))
        self.assertIn("forecast_training_ready=all(", source)
        self.assertIn('and forecast_training_ready else "degraded"', source)
        self.assertIn('"training_ready_by_horizon":forecast_training_ready_by_horizon', source)

    def test_forecast_readiness_uses_actual_horizon_training_requirements(self):
        source = ast.get_source_segment(SOURCE, find_function("_ensemble_forecast"))
        self.assertIn("training_ready_by_horizon[str(h)]=len(y)>=minimum_history", source)
        self.assertIn("minimum_training_origins=32", source)
        plan = ast.get_source_segment(SOURCE, find_function("forecast_plan"))
        self.assertIn('ready":bool(ready_by_horizon) and all(ready_by_horizon.values())', plan)
        self.assertIn('models["btc"]={"model":"unverified_user_input_fallback"', plan)
        quality = ast.get_source_segment(SOURCE, find_function("_forecast_quality"))
        self.assertIn('"model_readiness_by_horizon":readiness_by_horizon', quality)

    def test_fresh_market_data_is_not_misreported_as_empirical_forecast_validation(self):
        source = ast.get_source_segment(SOURCE, find_function("_forecast_quality"))
        self.assertIn('"status":"data_ready_unvalidated"', source)
        self.assertIn("independent 1h/6h/24h/72h out-of-sample release validation is required", source)

    def test_dispatch_requires_fresh_provenance_verified_market_data(self):
        source = ast.get_source_segment(SOURCE, find_function("dispatch_plan"))
        self.assertIn('_market_snapshot_freshness(c)', source)
        self.assertIn('_eligible_market_rows(c,24,require_site_tariff=False)', source)
        self.assertIn('forecast_source="input_fallback_unverified"', source)
        self.assertIn('and forecast_training_ready else "degraded"', source)
        self.assertIn('"forecast_training_ready":forecast_training_ready', source)
        self.assertIn('"empirical_forecast_release_gate":{"status":"blocked"', source)

    def test_single_strategy_dominance_is_not_reported_as_validated_regret(self):
        source = ast.get_source_segment(SOURCE, find_function("decision_quality"))
        self.assertIn("low_information_single_strategy_dominance", source)
        self.assertIn('len(chosen_strategies)<2 else "validated"', source)
        self.assertIn('"distinct_strategies_selected":len(chosen_strategies)', source)

    def test_regret_simulation_uses_mean_preserving_lognormal_shocks(self):
        source = ast.get_source_segment(SOURCE, find_function("regret_analysis"))
        self.assertIn("btc_sigma=0.20; gpu_sigma=0.20; energy_sigma=0.13", source)
        self.assertIn("rng.lognormvariate(-0.5*btc_sigma*btc_sigma,btc_sigma)", source)
        self.assertIn("rng.lognormvariate(-0.5*energy_sigma*energy_sigma,energy_sigma)", source)

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
