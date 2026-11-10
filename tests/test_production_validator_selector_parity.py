"""Regression tests proving production and independent validator selectors stay aligned."""
import ast
import math
import statistics
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PRODUCTION_SOURCE = (ROOT / "saas_api.py").read_text(encoding="utf-8")
VALIDATOR_SOURCE = (ROOT / "scripts" / "economic_validation.py").read_text(encoding="utf-8")


def function_node(source, name):
    tree = ast.parse(source)
    matches = [node for node in ast.walk(tree)
               if isinstance(node, ast.FunctionDef) and node.name == name]
    if not matches:
        raise AssertionError(f"Missing function: {name}")
    return matches[-1] if name == "_horizon_candidate_values" else matches[0]


def load_functions(source, names):
    nodes = [function_node(source, name) for name in names]
    namespace = {"math": math, "statistics": statistics}
    module = ast.Module(body=nodes, type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), "<selector-parity>", "exec"), namespace)
    return namespace


class ProductionValidatorSelectorParityTests(unittest.TestCase):
    def setUp(self):
        helpers = ("_horizon_candidate_values", "_btc_price_network_candidate")
        self.production = load_functions(
            PRODUCTION_SOURCE, helpers + ("_adaptive_horizon_weights",))
        self.validator = load_functions(
            VALIDATOR_SOURCE, helpers + ("corrected_forecast_weights",))

    @staticmethod
    def fixture(length, exogenous=False):
        values = []
        prices = []
        network = []
        for i in range(length):
            # Deterministic, nonstationary but bounded market-like series.
            values.append(40.0 + 0.0015 * i + 0.7 * math.sin(i / 37.0)
                          + 0.12 * math.sin(i / 5.0))
            prices.append(60000.0 + 2.0 * i + 120.0 * math.sin(i / 53.0))
            network.append(650.0 + 0.004 * i + 3.0 * math.sin(i / 71.0))
        return values, (prices if exogenous else None), (network if exogenous else None)

    def assert_selector_parity(self, sample_steps, exogenous=False):
        values, prices, network = self.fixture(7000, exogenous=exogenous)
        production_weights = self.production["_adaptive_horizon_weights"](
            values, sample_steps, 300.0, log_scale=exogenous,
            btc_price_values=prices, network_values=network)
        validator_diagnostics = {}
        validator_weights = self.validator["corrected_forecast_weights"](
            values, sample_steps, 300.0, log_scale=exogenous,
            btc_price_values=prices, network_values=network,
            diagnostics=validator_diagnostics,
            network_source="test_fixture" if exogenous else "unavailable")
        self.assertEqual(production_weights, validator_weights)
        self.assertEqual(sum(production_weights.values()), 1.0)
        self.assertEqual(validator_diagnostics["training_origins"] >= 32, True)
        self.assertLessEqual(validator_diagnostics["training_origins"], 120)

    def test_production_and_validator_select_same_gpu_candidates_at_each_horizon(self):
        for sample_steps in (1, 6, 12, 72, 288, 864):
            with self.subTest(sample_steps=sample_steps):
                self.assert_selector_parity(sample_steps)

    def test_production_and_validator_select_same_btc_candidates_with_exogenous_inputs(self):
        for sample_steps in (1, 72, 288, 864):
            with self.subTest(sample_steps=sample_steps):
                self.assert_selector_parity(sample_steps, exogenous=True)

    def test_both_selectors_fail_closed_before_32_separated_origins_exist(self):
        sample_steps = 288
        stride = math.ceil(sample_steps / 8)
        minimum_feature_history_steps = 48 * 12
        values, prices, network = self.fixture(minimum_feature_history_steps + sample_steps + 32 * stride - 1)
        production_weights = self.production["_adaptive_horizon_weights"](
            values, sample_steps, 300.0)
        validator_weights = self.validator["corrected_forecast_weights"](
            values, sample_steps, 300.0)
        self.assertEqual(production_weights["last_value"], 1.0)
        self.assertEqual(validator_weights["last_value"], 1.0)
        self.assertEqual(sum(production_weights.values()), 1.0)
        self.assertEqual(sum(validator_weights.values()), 1.0)


if __name__ == "__main__":
    unittest.main()
