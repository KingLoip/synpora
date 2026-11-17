"""Source-level safety contract for production dispatch.

The early CI unit-test phase intentionally has no FastAPI dependency installed.
"""
from pathlib import Path
import unittest


class DispatchFailClosedContractTests(unittest.TestCase):
    def _dispatch_block(self):
        source = Path("saas_api.py").read_text(encoding="utf-8")
        start = source.index("dispatch_quality={")
        end = source.index("\n        return {", start)
        return source[start:end]

    def test_fresh_market_provenance_cannot_mark_dispatch_validated(self):
        block = self._dispatch_block()
        self.assertIn('"status":"unvalidated" if forecast_source=="adaptive_market_with_site_tariff_input" else "degraded"', block)
        self.assertIn('"empirical_forecast_release_gate":{"status":"blocked"', block)
        self.assertNotIn('"status":"validated"', block)

    def test_release_gate_explains_missing_empirical_evidence(self):
        block = self._dispatch_block()
        self.assertIn("No independently validated all-horizon release result", block)
        self.assertIn("Fresh market data and persistence fallbacks do not qualify", block)


    def test_regret_log_normal_shocks_are_mean_preserving_without_floor_bias(self):
        source = Path("saas_api.py").read_text(encoding="utf-8")
        self.assertIn("btc_sigma=0.20; gpu_sigma=0.20; energy_sigma=0.13", source)
        self.assertIn("rng.lognormvariate(-0.5*shock*shock,shock)", source)
        self.assertIn("rng.lognormvariate(-0.5*energy_shock*energy_shock,energy_shock)", source)
        self.assertNotIn("btc=max(0.01,float(x.btc_hashprice_usd_ph_day)*rng.lognormvariate", source)
        self.assertNotIn("gpu=max(0.01,float(x.gpu_hourly_usd)*rng.lognormvariate", source)


if __name__ == "__main__":
    unittest.main()
