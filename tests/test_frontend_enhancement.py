"""Regression checks for the browser enhancement injected into the frontend."""
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "synpora_enhancement.py"


def browser_script():
    source = SOURCE.read_text(encoding="utf-8")
    match = re.search(r'SCRIPT\s*=\s*r"""(.*?)"""', source, re.DOTALL)
    if not match:
        raise AssertionError("Could not find the browser enhancement SCRIPT block")
    script = match.group(1).strip()
    if script.startswith("<script>"):
        script = script[len("<script>"):]
    if script.endswith("</script>"):
        script = script[:-len("</script>")]
    return script.strip()


class FrontendEnhancementTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = SOURCE.read_text(encoding="utf-8")
        cls.script = browser_script()

    def test_demo_metrics_are_explicitly_illustrative(self):
        self.assertIn("Illustrative demo data — not live telemetry.", self.script)
        self.assertIn("not a verified result", self.script)
        self.assertIn("not automatic hardware control", self.script)

    def test_account_flow_uses_password_field_not_browser_prompt(self):
        self.assertIn('id="sp-password"', self.script)
        self.assertIn('document.getElementById("sp-password").value', self.script)
        self.assertNotIn('window.prompt("Passwort für den Pilot-Account', self.script)

    def test_email_verification_response_is_handled(self):
        self.assertIn("data.verification_required", self.script)
        self.assertIn("Please verify your email", self.script)
        self.assertIn("check your email for the verification link", self.script.lower())

    def test_legacy_bootstrap_password_ui_is_replaced(self):
        self.assertIn("/railway bootstrap/i", self.script)
        self.assertIn("Open workspace", self.script)
        self.assertNotIn("dein Railway Bootstrap-Passwort", self.script)

    def test_empty_data_states_explain_what_is_missing(self):
        self.assertIn("required market inputs and assumptions are available", self.script)
        self.assertIn("unknown or stale data is not treated as verified", self.script)

    def test_javascript_syntax(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("Node.js is not available in this environment")
        with tempfile.NamedTemporaryFile("w", suffix=".js", encoding="utf-8") as tmp:
            tmp.write(self.script)
            tmp.flush()
            result = subprocess.run(
                [node, "--check", tmp.name],
                capture_output=True,
                text=True,
                check=False,
            )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)


if __name__ == "__main__":
    unittest.main()
