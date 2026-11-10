"""Production release and Monte-Carlo contract regression tests.

These tests deliberately inspect production route implementations so that a
future refactor cannot confuse model readiness with empirical release approval
or let the risk endpoints drift to different shock distributions.
"""
import ast
import math
import random
import statistics
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "saas_api.py").read_text(encoding="utf-8")
TREE = ast.parse(SOURCE)


def function_source(name):
    matches = [
        node for node in ast.walk(TREE)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == name
    ]
    if not matches:
        raise AssertionError(f"Missing production function: {name}")
    return ast.get_source_segment(SOURCE, matches[0])


class ProductionReleaseContractTests(unittest.TestCase):
    def test_market_quality_preserves_provider_observation_timestamps(self):
        source = function_source("_market_quality")
        self.assertIn('"observed_at":float(meta.get("observed_at"))', source)
        self.assertIn('"age_seconds":round(max(0.0,age),1)', source)

    def test_production_history_deduplicates_non_monotonic_source_observations(self):
        source = function_source("_eligible_market_rows")
        self.assertIn("_optional_external_market_observed_at", source)
        self.assertIn("last_btc_observed_at", source)
        self.assertIn("last_gpu_observed_at", source)

    def test_dispatch_training_readiness_never_means_empirical_release(self):
        source = function_source("dispatch_plan")
        self.assertIn("forecast_training_ready=all(", source)
        self.assertIn('"status":"unvalidated" if forecast_source=="adaptive_market_with_site_tariff_input" and forecast_training_ready else "degraded"', source)
        self.assertIn('"empirical_forecast_release_gate":{"status":"blocked"', source)
        self.assertIn("No independently validated all-horizon release result", source)
        self.assertIn('"recommendation_only":True', source)
        self.assertIn('"hardware_write":False', source)

    def test_data_quality_does_not_claim_empirical_forecast_skill(self):
        source = function_source("_forecast_quality")
        self.assertIn('"status":"data_ready_unvalidated"', source)
        self.assertIn("independent 1h/6h/24h/72h out-of-sample release validation is required", source)
        self.assertNotIn('"status":"validated"', source)

    def test_risk_analysis_and_decision_engine_share_mean_preserving_shocks(self):
        for name in ("risk_analysis", "decision_engine"):
            source = function_source(name)
            self.assertIn("energy_shock=shock*0.65", source)
            self.assertGreaterEqual(source.count("rng.lognormvariate(-0.5*shock*shock,shock)"), 2)
            self.assertIn("rng.lognormvariate(-0.5*energy_shock*energy_shock,energy_shock)", source)

    def test_regret_uses_the_same_btc_gpu_energy_volatility_assumptions(self):
        source = function_source("regret_analysis")
        self.assertIn("btc_sigma=0.20; gpu_sigma=0.20; energy_sigma=0.13", source)
        self.assertIn("rng.lognormvariate(-0.5*btc_sigma*btc_sigma,btc_sigma)", source)
        self.assertIn("rng.lognormvariate(-0.5*gpu_sigma*gpu_sigma,gpu_sigma)", source)
        self.assertIn("rng.lognormvariate(-0.5*energy_sigma*energy_sigma,energy_sigma)", source)
        self.assertIn('"mean_preserving":True', source)
        self.assertIn('"regret_evidence_validated":False', source)

    def test_mean_preserving_lognormal_multipliers_have_unit_expected_value(self):
        rng = random.Random(20261110)
        for sigma in (0.20, 0.20, 0.13):
            values = [
                rng.lognormvariate(-0.5 * sigma * sigma, sigma)
                for _ in range(50000)
            ]
            self.assertAlmostEqual(statistics.fmean(values), 1.0, delta=0.01)


if __name__ == "__main__":
    unittest.main()
