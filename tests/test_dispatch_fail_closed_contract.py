"""Source-level safety contract for production dispatch.

The early CI unit-test phase intentionally has no FastAPI dependency installed.
"""
from pathlib import Path
import unittest


class DispatchFailClosedContractTests(unittest.TestCase):
    def test_fresh_market_provenance_cannot_mark_dispatch_validated(self):
        source = Path("saas_api.py").read_text(encoding="utf-8")
        start = source.index('dispatch_quality={', source.index('def dispatch('))
        end = source.index('\n        return {', start)
        block = source[start:end]
        self.assertIn('"status":"unvalidated" if forecast_source=="adaptive_market_with_site_tariff_input" else "degraded"', block)
        self.assertIn('"empirical_forecast_release_gate":{"status":"blocked"', block)
        self.assertNotIn('"status":"validated"', block)

    def test_release_gate_requires_all_four_horizons_and_real_oos_thresholds(self):
        source = Path("saas_api.py").read_text(encoding="utf-8")
        start = source.index('dispatch_quality={', source.index('def dispatch('))
        end = source.index('\n        return {', start)
        block = source[start:end]
        self.assertIn('No independently validated all-horizon release result', block)
        self.assertIn('Fresh market data and persistence fallbacks do not qualify', block)


if __name__ == "__main__":
    unittest.main()
