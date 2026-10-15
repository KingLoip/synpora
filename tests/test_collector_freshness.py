import ast
import unittest
from pathlib import Path


class CollectorFreshnessContractTests(unittest.TestCase):
    def test_collection_interval_keeps_three_minute_freshness_buffer(self):
        source = Path(__file__).resolve().parents[1] / "server.py"
        tree = ast.parse(source.read_text(encoding="utf-8"))
        values = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id == "MARKET_COLLECTION_INTERVAL_SECONDS":
                        values[target.id] = ast.literal_eval(node.value)

        interval = values.get("MARKET_COLLECTION_INTERVAL_SECONDS")
        self.assertIsNotNone(interval, "collector interval must be explicitly configured")
        self.assertGreater(interval, 0)
        self.assertLessEqual(interval, 720, "keep at least a three-minute buffer under the 15-minute freshness gate")
        self.assertLess(interval, 900, "collector interval must remain below the global market freshness limit")


if __name__ == "__main__":
    unittest.main()
