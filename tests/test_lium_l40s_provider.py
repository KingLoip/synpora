import unittest
from datetime import datetime, timedelta, timezone

from saas_api import _lium_l40s_price_observation


def timestamp_at(moment):
    return moment.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


class LiumL40SProviderTests(unittest.TestCase):
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
