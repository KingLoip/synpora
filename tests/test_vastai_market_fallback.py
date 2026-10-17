import unittest
from io import BytesIO
from unittest.mock import Mock
from urllib.error import HTTPError

from saas_api import _vastai_l40s_offers


def bad_request(code):
    return HTTPError("https://cloud.vast.ai/api/v0/bundles/", code, "request rejected", None, BytesIO(b"{}"))


class VastAIOfferFallbackTests(unittest.TestCase):
    def test_get_400_falls_back_to_documented_post_filter(self):
        get_json = Mock(side_effect=[bad_request(400), bad_request(400)])
        expected = [{"gpu_name": "NVIDIA L40S", "dph_total": 1.25}]
        post_json = Mock(return_value={"offers": expected})

        offers = _vastai_l40s_offers("test-key", get_json, post_json)

        self.assertEqual(offers, expected)
        self.assertEqual(get_json.call_count, 2)
        post_args, post_kwargs = post_json.call_args
        self.assertEqual(post_args[0], "https://console.vast.ai/api/v0/bundles/")
        self.assertEqual(post_args[1]["type"], "ondemand")
        self.assertEqual(post_args[1]["rentable"], {"eq": True})
        self.assertEqual(post_args[1]["limit"], 100)
        self.assertIn("L40S", post_args[1]["gpu_name"]["in"])
        self.assertEqual(post_kwargs["ttl_seconds"], 300)
        self.assertNotIn("test-key", repr(post_args[1]))

    def test_unusable_nonempty_get_responses_fall_through_to_post(self):
        # A provider can return a non-empty response that still contains no
        # usable L40S quote. Such a response must not suppress the POST fallback.
        get_json = Mock(side_effect=[
            {"offers": [{"gpu_name": "NVIDIA A100", "dph_total": 1.25}]},
            {"offers": [{"gpu_name": "NVIDIA L40S", "dph_total": 0, "rentable": True, "type": "ondemand"}]},
        ])
        expected = [{"gpu_name": "NVIDIA L40S", "dph_total": 0.95}]
        post_json = Mock(return_value={"offers": expected})

        offers = _vastai_l40s_offers("test-key", get_json, post_json)

        self.assertEqual(offers, expected)
        self.assertEqual(get_json.call_count, 2)
        post_json.assert_called_once()
        post_args, _ = post_json.call_args
        self.assertNotIn("test-key", repr(post_args[1]))

    def test_nonrentable_or_bid_l40s_offer_does_not_suppress_fallback(self):
        get_json = Mock(side_effect=[
            {"offers": [{"gpu_name": "NVIDIA L40S", "dph_total": 0.9, "rentable": False, "type": "bid"}]},
            {"offers": []},
        ])
        expected = [{"gpu_name": "NVIDIA L40S", "dph_total": 0.95}]
        post_json = Mock(return_value={"offers": expected})

        offers = _vastai_l40s_offers("test-key", get_json, post_json)

        self.assertEqual(offers, expected)
        self.assertEqual(get_json.call_count, 2)
        post_json.assert_called_once()

    def test_successful_get_avoids_post(self):
        expected = [{"gpu_name": "L40S", "dph_total": 0.9}]
        get_json = Mock(return_value={"offers": expected})
        post_json = Mock()

        offers = _vastai_l40s_offers("test-key", get_json, post_json)

        self.assertEqual(offers, expected)
        self.assertEqual(get_json.call_count, 1)
        post_json.assert_not_called()

    def test_auth_failure_does_not_fallback_or_mask_error(self):
        get_json = Mock(side_effect=bad_request(403))
        post_json = Mock()

        with self.assertRaises(HTTPError) as raised:
            _vastai_l40s_offers("test-key", get_json, post_json)

        self.assertEqual(raised.exception.code, 403)
        post_json.assert_not_called()


if __name__ == "__main__":
    unittest.main()
