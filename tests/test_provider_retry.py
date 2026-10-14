import json
import unittest
from io import BytesIO
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from saas_api import _provider_request_json, _provider_error_label


def response(payload):
    return BytesIO(json.dumps(payload).encode("utf-8"))


class ProviderRetryTests(unittest.TestCase):
    @patch("time.sleep")
    @patch("urllib.request.urlopen")
    def test_retries_transient_network_error_then_succeeds(self, urlopen, sleep):
        urlopen.side_effect = [URLError("temporary network failure"), response({"ok": True})]

        result = _provider_request_json("https://provider.example/data", attempts=3)

        self.assertEqual(result, {"ok": True})
        self.assertEqual(urlopen.call_count, 2)
        sleep.assert_called_once_with(0.25)

    @patch("time.sleep")
    @patch("urllib.request.urlopen")
    def test_retries_server_error_then_succeeds(self, urlopen, sleep):
        error = HTTPError("https://provider.example/data", 503, "unavailable", {}, BytesIO(b""))
        urlopen.side_effect = [error, response({"price": 1.25})]

        result = _provider_request_json("https://provider.example/data", attempts=3)

        self.assertEqual(result, {"price": 1.25})
        self.assertEqual(urlopen.call_count, 2)
        sleep.assert_called_once_with(0.25)

    @patch("time.sleep")
    @patch("urllib.request.urlopen")
    def test_does_not_retry_authentication_or_other_permanent_4xx(self, urlopen, sleep):
        error = HTTPError("https://provider.example/data", 403, "forbidden", {}, BytesIO(b""))
        urlopen.side_effect = error

        with self.assertRaises(HTTPError):
            _provider_request_json("https://provider.example/data", attempts=3)

        urlopen.assert_called_once()
        sleep.assert_not_called()

    @patch("time.sleep")
    @patch("urllib.request.urlopen")
    def test_retry_budget_is_bounded_to_three_attempts(self, urlopen, sleep):
        urlopen.side_effect = URLError("temporary network failure")

        with self.assertRaises(URLError):
            _provider_request_json("https://provider.example/data", attempts=99)

        self.assertEqual(urlopen.call_count, 3)
        self.assertEqual(sleep.call_count, 2)


    @patch("time.sleep")
    @patch("urllib.request.urlopen")
    def test_rate_limit_respects_short_retry_after(self, urlopen, sleep):
        error = HTTPError("https://provider.example/data", 429, "rate limited",
                          {"Retry-After": "0.5"}, BytesIO(b""))
        urlopen.side_effect = [error, response({"ok": True})]

        result = _provider_request_json("https://provider.example/data", attempts=3)

        self.assertEqual(result, {"ok": True})
        self.assertEqual(urlopen.call_count, 2)
        sleep.assert_called_once_with(0.5)

    @patch("time.sleep")
    @patch("urllib.request.urlopen")
    def test_rate_limit_with_long_retry_after_is_deferred(self, urlopen, sleep):
        error = HTTPError("https://provider.example/data", 429, "rate limited",
                          {"Retry-After": "60"}, BytesIO(b""))
        urlopen.side_effect = error

        with self.assertRaises(HTTPError):
            _provider_request_json("https://provider.example/data", timeout=5, attempts=3)

        urlopen.assert_called_once()
        sleep.assert_not_called()


    @patch("time.sleep")
    @patch("urllib.request.urlopen")
    def test_malformed_retry_after_preserves_http_error(self, urlopen, sleep):
        error = HTTPError("https://provider.example/data", 429, "rate limited",
                          {"Retry-After": "not-a-date"}, BytesIO(b""))
        urlopen.side_effect = error

        with self.assertRaises(HTTPError) as caught:
            _provider_request_json("https://provider.example/data", attempts=3)

        self.assertEqual(caught.exception.code, 429)
        urlopen.assert_called_once()
        sleep.assert_not_called()

    def test_error_label_reports_http_status_without_response_body(self):
        error = HTTPError("https://provider.example/data?token=secret", 429, "rate limited", {}, BytesIO(b"secret-body"))

        self.assertEqual(_provider_error_label(error), "HTTP 429")
        self.assertNotIn("secret", _provider_error_label(error))


if __name__ == "__main__":
    unittest.main()
