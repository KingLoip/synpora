import json
import os
import unittest
from unittest.mock import patch, Mock
from urllib.error import HTTPError
from io import BytesIO

from saas_api import _email_provider_settings, _resend_api_request, _send_resend_email


class ResendEmailProviderTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {
            "SYNPORA_EMAIL_PROVIDER": "resend",
            "SYNPORA_RESEND_API_KEY": "re_test_secret",
            "SYNPORA_EMAIL_FROM": "SYNPORA <alerts@example.com>",
            "SYNPORA_PUBLIC_URL": "https://synpora.example.com",
            "SYNPORA_SMTP_HOST": "",
            "SYNPORA_SMTP_USERNAME": "",
            "SYNPORA_SMTP_PASSWORD": "",
            "SYNPORA_SMTP_FROM": "",
        }, clear=False)
        self.env.start()

    def tearDown(self):
        self.env.stop()

    def test_resend_configuration_is_ready_without_smtp(self):
        settings = _email_provider_settings()
        self.assertEqual(settings["provider"], "resend")
        self.assertTrue(settings["configured"])
        self.assertTrue(settings["resend_configured"])
        self.assertFalse(settings["smtp_configured"])
        self.assertEqual(settings["sender"], "SYNPORA <alerts@example.com>")

    def test_forced_resend_does_not_fall_back_to_smtp(self):
        os.environ.pop("SYNPORA_RESEND_API_KEY", None)
        os.environ.update({
            "SYNPORA_SMTP_HOST": "smtp.example.com",
            "SYNPORA_SMTP_USERNAME": "smtp-user",
            "SYNPORA_SMTP_PASSWORD": "smtp-password",
            "SYNPORA_SMTP_FROM": "SYNPORA <alerts@example.com>",
        })
        settings = _email_provider_settings()
        self.assertEqual(settings["provider"], "resend")
        self.assertFalse(settings["configured"])
        self.assertTrue(settings["smtp_configured"])

    @patch("urllib.request.urlopen")
    def test_resend_send_posts_only_expected_message_fields(self, urlopen):
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.return_value = b'{"id":"email_test_123"}'
        urlopen.return_value = response

        result = _send_resend_email(
            "re_test_secret", "SYNPORA <alerts@example.com>",
            "person@example.net", "Verify your SYNPORA email", "one-time verification link",
        )

        self.assertEqual(result, {"email_id": "email_test_123"})
        request = urlopen.call_args.args[0]
        self.assertEqual(request.full_url, "https://api.resend.com/emails")
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(request.get_header("Authorization"), "Bearer re_test_secret")
        payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual(payload["to"], ["person@example.net"])
        self.assertEqual(payload["subject"], "Verify your SYNPORA email")
        self.assertNotIn("api_key", payload)

    @patch("urllib.request.urlopen")
    def test_provider_error_does_not_leak_response_body(self, urlopen):
        urlopen.side_effect = HTTPError(
            "https://api.resend.com/emails", 401, "Unauthorized", None,
            BytesIO(b'{"message":"secret provider detail"}'),
        )
        with self.assertRaisesRegex(RuntimeError, "Email provider HTTP 401") as raised:
            _resend_api_request("GET", "/domains", "re_test_secret")
        self.assertNotIn("secret provider detail", str(raised.exception))
        self.assertNotIn("re_test_secret", str(raised.exception))

    def test_sender_and_https_are_required(self):
        os.environ["SYNPORA_EMAIL_FROM"] = ""
        self.assertFalse(_email_provider_settings()["configured"])
        os.environ["SYNPORA_EMAIL_FROM"] = "alerts@example.com"
        os.environ["SYNPORA_PUBLIC_URL"] = "http://synpora.example.com"
        self.assertFalse(_email_provider_settings()["configured"])


if __name__ == "__main__":
    unittest.main()
