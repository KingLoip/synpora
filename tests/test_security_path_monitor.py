import unittest
from unittest.mock import Mock

from security_path_monitor import (
    SensitivePathMonitor,
    classify_sensitive_path,
    observe_sensitive_path,
)


class SensitivePathClassificationTests(unittest.TestCase):
    def test_detects_sensitive_paths(self):
        cases = {
            "/.env": "environment",
            "/.env.production": "environment",
            "/.env.production.backup": "environment",
            "/.git/config": "version_control",
            "/.aws/credentials": "cloud_credentials",
            "/.ssh/id_rsa": "cloud_credentials",
            "/.claude/.credentials.json": "developer_credentials",
            "/.cargo/credentials.toml": "developer_credentials",
            "/.gem/credentials": "developer_credentials",
            "/.dbeaver/credentials-config.json": "developer_credentials",
            "/wp-config.php": "php_diagnostics",
            "/wp-config.php.bak": "php_diagnostics",
            "/wp-config.php.save": "php_diagnostics",
            "/symfony/_profiler/phpinfo": "php_diagnostics",
            "/phpinfo.php": "php_diagnostics",
            "/server-status": "server_diagnostics",
            "/backup/credentials.json": "credential_file",
            "/.credentials.yml": "credential_file",
        }
        for path, expected in cases.items():
            with self.subTest(path=path):
                self.assertEqual(classify_sensitive_path(path), expected)

    def test_decodes_repeated_encoding_and_windows_separators(self):
        self.assertEqual(
            classify_sensitive_path("/%252e%2567it%252fconfig"),
            "version_control",
        )
        self.assertEqual(classify_sensitive_path(r"\\.aws\\credentials"), "cloud_credentials")

    def test_ignores_ordinary_application_paths(self):
        for path in ("/", "/health", "/api/v1/configuration", "/assets/app.js", "/docs"):
            with self.subTest(path=path):
                self.assertIsNone(classify_sensitive_path(path))


class SensitivePathMonitorTests(unittest.TestCase):
    def test_expected_404_scans_are_quiet_until_threshold(self):
        monitor = SensitivePathMonitor(threshold=3, window_seconds=60, salt=b"test")
        self.assertIsNone(monitor.record("192.0.2.1", "/.env", 404, now=1))
        self.assertIsNone(monitor.record("192.0.2.1", "/.git/config", 404, now=2))
        alert = monitor.record("192.0.2.1", "/.aws/credentials", 404, now=3)
        self.assertEqual(alert["reason"], "repeated_reconnaissance")
        self.assertEqual(alert["count_in_window"], 3)
        # One warning per threshold crossing, not one warning for every subsequent probe.
        self.assertIsNone(monitor.record("192.0.2.1", "/.env", 404, now=4))

    def test_successful_sensitive_path_response_alerts_immediately(self):
        monitor = SensitivePathMonitor(salt=b"test")
        alert = monitor.record("192.0.2.2", "/.git/config", 200, now=1)
        self.assertEqual(alert["reason"], "unexpected_success")
        self.assertEqual(alert["status_code"], 200)
        redirect = monitor.record("192.0.2.3", "/.env", 302, now=1)
        self.assertEqual(redirect["reason"], "unexpected_success")
        self.assertIsNone(monitor.record("192.0.2.2", "/.env", 200, now=2))
        self.assertIsNone(monitor.record("192.0.2.3", "/.env", 302, now=2))
        reset_monitor = SensitivePathMonitor(window_seconds=30, salt=b"test")
        self.assertEqual(reset_monitor.record("192.0.2.4", "/.env", 200, now=1)["reason"], "unexpected_success")
        self.assertIsNone(reset_monitor.record("192.0.2.4", "/.env", 200, now=2))
        self.assertEqual(reset_monitor.record("192.0.2.4", "/.env", 200, now=33)["reason"], "unexpected_success")

    def test_window_expiry_resets_burst_count(self):
        monitor = SensitivePathMonitor(threshold=2, window_seconds=30, salt=b"test")
        self.assertIsNone(monitor.record("source", "/.env", 404, now=1))
        self.assertIsNone(monitor.record("source", "/.env", 404, now=32))
        self.assertEqual(
            monitor.record("source", "/.env", 404, now=33)["count_in_window"], 2
        )

    def test_source_fingerprint_is_not_raw_ip(self):
        monitor = SensitivePathMonitor(salt=b"test")
        alert = monitor.record("203.0.113.77", "/.env", 200, now=1)
        self.assertNotIn("203.0.113.77", str(alert))
        self.assertEqual(len(alert["source_fingerprint"]), 12)

    def test_logs_never_include_raw_source_or_sensitive_path(self):
        logger = Mock()
        source = "198.51.100.201"
        path = "/.env.production.backup"
        observe_sensitive_path(source, path, 200, logger=logger)
        logger.warning.assert_called_once()
        logged = repr(logger.warning.call_args)
        self.assertNotIn(source, logged)
        self.assertNotIn(path, logged)
        self.assertIn("source_fingerprint", logged)

    def test_source_cardinality_is_bounded(self):
        monitor = SensitivePathMonitor(max_sources=32, salt=b"test")
        for index in range(100):
            monitor.record("source-%s" % index, "/.env", 404, now=index)
        self.assertLessEqual(len(monitor._sources), 32)
        self.assertTrue(all(len(state[0]) <= monitor.threshold for state in monitor._sources.values()))


    def test_observed_128_probe_wave_emits_one_bounded_alert(self):
        monitor = SensitivePathMonitor(threshold=10, window_seconds=300, salt=b"test")
        alerts = []
        paths = ("/.env", "/.git/config", "/.aws/credentials", "/.env.production")
        for index in range(128):
            alert = monitor.record("scanner-source", paths[index % len(paths)], 404, now=100 + index / 10)
            if alert is not None:
                alerts.append(alert)
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0]["reason"], "repeated_reconnaissance")
        self.assertEqual(alerts[0]["count_in_window"], 10)
        self.assertLessEqual(len(monitor._sources), 32)


if __name__ == "__main__":
    unittest.main()
