import unittest
from unittest.mock import patch
from datetime import datetime, timedelta, timezone

from saas_api import _is_fresh_public_gpu_observation, _lium_l40s_price_observation, _provider_request_json


def timestamp_at(moment):
    return moment.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


class LiumL40SProviderTests(unittest.TestCase):

    def test_provider_request_honors_fast_timeout_without_retry(self):
        with patch("urllib.request.urlopen", side_effect=TimeoutError("simulated timeout")) as mocked:
            with self.assertRaises(TimeoutError):
                _provider_request_json("https://example.invalid/provider", timeout=2, attempts=1)
        mocked.assert_called_once()
        self.assertEqual(mocked.call_args.kwargs["timeout"], 2)

    def test_fresh_lium_public_quote_prevents_keyed_fallback_override(self):
        self.assertTrue(_is_fresh_public_gpu_observation({
            "source": "external",
            "provider": "Lium public L40S rentable-node median",
            "age_seconds": 45,
            "valid": True,
        }))

    def test_stale_reference_or_unknown_provider_does_not_prevent_fallback(self):
        base = {
            "source": "external",
            "provider": "Lium public L40S rentable-node median",
            "age_seconds": 901,
            "valid": True,
        }
        self.assertFalse(_is_fresh_public_gpu_observation(base))
        base.update({"age_seconds": 45, "source": "reference"})
        self.assertFalse(_is_fresh_public_gpu_observation(base))
        base.update({"source": "external", "provider": "unknown"})
        self.assertFalse(_is_fresh_public_gpu_observation(base))

    def test_uses_median_of_fresh_available_l40s_offers_only(self):
        now = datetime(2026, 10, 15, 13, 40, tzinfo=timezone.utc).timestamp()
        payload = {
            "generated_at": timestamp_at(datetime.fromtimestamp(now, timezone.utc) - timedelta(seconds=45)),
            "nodes": [
                {"gpu_model": "L40S", "tier": "secure", "available_gpu_count": 1, "price_per_gpu_hour": 0.38},
                {"gpu_model": "NVIDIA L40S", "tier": "secure", "available_gpu_count": 2, "price_per_gpu_hour": "0.42"},
                {"gpu_model": "L40S", "tier": "spot", "available_gpu_count": 1, "price_per_gpu_hour": 0.10},
                {"gpu_model": "L40", "available_gpu_count": 1, "price_per_gpu_hour": 0.33},
                {"gpu_model": "L40S", "tier": "secure", "available_gpu_count": 0, "price_per_gpu_hour": 0.31},
                {"gpu_model": "L40S", "tier": "secure", "available_gpu_count": 1, "price_per_gpu_hour": 0},
                {"gpu_model": "L40S", "tier": "secure", "available_gpu_count": 1, "price_per_gpu_hour": "invalid"},
            ],
        }

        observation = _lium_l40s_price_observation(payload, now)

        self.assertIsNotNone(observation)
        self.assertAlmostEqual(observation["price_usd_per_gpu_hour"], 0.40)
        self.assertEqual(observation["offer_count"], 2)
        self.assertEqual(observation["age_seconds"], 45.0)

    def test_rejects_stale_feed(self):
        now = datetime(2026, 10, 15, 13, 40, tzinfo=timezone.utc).timestamp()
        payload = {
            "generated_at": timestamp_at(datetime.fromtimestamp(now, timezone.utc) - timedelta(seconds=901)),
            "nodes": [{"gpu_model": "L40S", "available_gpu_count": 1, "price_per_gpu_hour": 0.38}],
        }

        self.assertIsNone(_lium_l40s_price_observation(payload, now))

    def test_rejects_missing_timestamp_and_reference_only_nodes(self):
        now = datetime(2026, 10, 15, 13, 40, tzinfo=timezone.utc).timestamp()
        self.assertIsNone(_lium_l40s_price_observation({
            "nodes": [{"gpu_model": "L40S", "price_per_gpu_hour": 0.38}],
        }, now))
        self.assertIsNone(_lium_l40s_price_observation({
            "generated_at": timestamp_at(datetime.fromtimestamp(now, timezone.utc)),
            "nodes": [{"gpu_model": "L40S", "available_gpu_count": 0, "price_per_gpu_hour": 0.38}],
        }, now))


if __name__ == "__main__":
    unittest.main()
