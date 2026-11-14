"""Bounded, privacy-conscious detection of repeated sensitive-path reconnaissance."""
from collections import OrderedDict, deque
import hashlib
import hmac
import logging
import re
import secrets
import threading
import time
from urllib.parse import unquote

_CATEGORY_PATTERNS = (
    ("environment", re.compile(r"^\.env(?:\.|$)")),
    ("version_control", re.compile(r"^\.(?:git|svn|hg)(?:$|/)")),
    ("cloud_credentials", re.compile(r"^\.(?:aws|ssh)(?:$|/)")),
    ("php_diagnostics", re.compile(r"^(?:phpinfo\.php|wp-config\.php)$")),
    ("server_diagnostics", re.compile(r"^server-status$")),
    ("credential_file", re.compile(r"^(?:id_rsa|credentials|secrets|config\.php)$")),
)


def classify_sensitive_path(path):
    """Return a low-cardinality category; never return or retain the raw path."""
    value = str(path or "")
    for _ in range(3):
        decoded = unquote(value)
        if decoded == value:
            break
        value = decoded
    value = value.replace("\\", "/").lower()
    segments = [part for part in value.split("/") if part and part not in (".", "..")]
    for segment in segments:
        for category, pattern in _CATEGORY_PATTERNS:
            if pattern.search(segment):
                return category
    # Traversal components are discarded only for matching; no path is logged or retained.
    return None


class SensitivePathMonitor:
    """Process-local sliding-window counters with bounded cardinality and hashed sources."""

    def __init__(self, threshold=10, window_seconds=300, max_sources=2048, salt=None):
        self.threshold = max(2, int(threshold))
        self.window_seconds = max(30, int(window_seconds))
        self.max_sources = max(32, int(max_sources))
        self._salt = salt or secrets.token_bytes(32)
        self._sources = OrderedDict()
        self._lock = threading.Lock()

    def _fingerprint(self, source):
        # Hash source identifiers so logs and in-memory keys do not expose IP addresses.
        return hmac.new(self._salt, str(source or "unknown").encode("utf-8", "replace"),
                        hashlib.sha256).hexdigest()[:12]

    def record(self, source, path, status_code, now=None):
        category = classify_sensitive_path(path)
        if category is None:
            return None
        timestamp = time.monotonic() if now is None else float(now)
        fingerprint = self._fingerprint(source)
        with self._lock:
            state = self._sources.pop(fingerprint, None)
            if state is None:
                events, burst_alerted, success_alerted = deque(maxlen=self.threshold), False, False
            else:
                events, burst_alerted, success_alerted = state
            events = deque(
                (item for item in events if timestamp - item[0] <= self.window_seconds),
                maxlen=self.threshold,
            )
            if not events:
                success_alerted = False
            events.append((timestamp, category))
            count = len(events)
            if count < self.threshold:
                burst_alerted = False
            successful = 200 <= int(status_code) < 400
            burst = count >= self.threshold and not burst_alerted
            unexpected_success = successful and not success_alerted
            if burst:
                burst_alerted = True
            if unexpected_success:
                success_alerted = True
            self._sources[fingerprint] = (events, burst_alerted, success_alerted)
            while len(self._sources) > self.max_sources:
                self._sources.popitem(last=False)
            if not unexpected_success and not burst:
                return None
            return {
                "category": category,
                "source_fingerprint": fingerprint,
                "count_in_window": count,
                "window_seconds": self.window_seconds,
                "status_code": int(status_code),
                "reason": "unexpected_success" if unexpected_success else "repeated_reconnaissance",
            }


_default_monitor = SensitivePathMonitor()


def observe_sensitive_path(source, path, status_code, logger=None):
    alert = _default_monitor.record(source, path, status_code)
    if alert is not None:
        (logger or logging.getLogger("synpora.security")).warning(
            "Sensitive-path reconnaissance detected; category=%s source_fingerprint=%s "
            "count_in_window=%s window_seconds=%s status_code=%s reason=%s",
            alert["category"], alert["source_fingerprint"], alert["count_in_window"],
            alert["window_seconds"], alert["status_code"], alert["reason"],
        )
    return alert
