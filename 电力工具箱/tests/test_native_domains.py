"""Shared domain transport contracts; domain suites prove numerical parity."""
import os
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

import toolbox_native_domain as domain


class NativeDomainTransportTests(unittest.TestCase):
    def test_python_mode_does_not_probe_or_start_native_process(self):
        with patch.dict(os.environ, TOOLBOX_DOMAIN_BACKEND="python"), patch.object(domain, "native_binary") as binary:
            self.assertIsNone(domain.invoke("sections", "calculate", {}))
            binary.assert_not_called()

    def test_auto_missing_binary_and_strict_mode_are_distinct(self):
        with patch.object(domain, "native_binary", return_value=None):
            with patch.dict(os.environ, TOOLBOX_DOMAIN_BACKEND="auto"):
                self.assertIsNone(domain.invoke("sections", "calculate", {}))
            with patch.dict(os.environ, TOOLBOX_DOMAIN_BACKEND="rust"):
                with self.assertRaisesRegex(RuntimeError, "Rust"):
                    domain.invoke("sections", "calculate", {})

    def test_result_is_unwrapped_and_action_is_sent_once(self):
        with patch.dict(os.environ, TOOLBOX_DOMAIN_BACKEND="rust"), \
             patch.object(domain, "native_binary", return_value=Path("helper.exe")), \
             patch.object(domain, "call", return_value={"protocol": 1, "result": {"rows": [0, None]}}) as call:
            self.assertEqual(domain.invoke("trading", "summary", {"sheets": []}), {"rows": [0, None]})
            call.assert_called_once_with(Path("helper.exe"), "trading", "", payload={"action": "summary", "sheets": []})

    def test_invalid_result_is_not_accepted_as_success(self):
        with patch.dict(os.environ, TOOLBOX_DOMAIN_BACKEND="rust"), \
             patch.object(domain, "native_binary", return_value=Path("helper.exe")), \
             patch.object(domain, "call", return_value={"protocol": 1}):
            with self.assertRaisesRegex(ValueError, "result"):
                domain.invoke("forecast", "features", {})

    def test_strict_errors_propagate_auto_warning_does_not_echo_input(self):
        with patch.object(domain, "native_binary", return_value=Path("helper.exe")), \
             patch.object(domain, "call", side_effect=ValueError("secret-token-example")):
            with patch.dict(os.environ, TOOLBOX_DOMAIN_BACKEND="rust"):
                with self.assertRaisesRegex(ValueError, "secret-token-example"):
                    domain.invoke("market_payload", "parse", {})
            with patch.dict(os.environ, TOOLBOX_DOMAIN_BACKEND="auto"):
                with self.assertWarns(RuntimeWarning) as caught:
                    self.assertIsNone(domain.invoke("market_payload", "parse", {}))
                self.assertNotIn("secret-token-example", str(caught.warning))

    def test_invalid_mode_and_reserved_action_are_rejected(self):
        with patch.dict(os.environ, TOOLBOX_DOMAIN_BACKEND="invalid"):
            with self.assertRaises(ValueError):
                domain.invoke("sections", "calculate", {})
        with patch.dict(os.environ, TOOLBOX_DOMAIN_BACKEND="rust"):
            with self.assertRaises(ValueError):
                domain.invoke("sections", "calculate", {"action": "other"})

    def test_side_effects_never_fall_back_after_ambiguous_failure(self):
        with patch.dict(os.environ, TOOLBOX_DOMAIN_BACKEND="auto"), \
             patch.object(domain, "native_binary", return_value=Path("helper.exe")), \
             patch.object(domain, "call", side_effect=subprocess.SubprocessError("lost response")):
            with self.assertRaises(subprocess.SubprocessError):
                domain.invoke("workbook_ops", "commit", {}, side_effects=True)

    def test_native_validation_error_matches_streaming_and_python_contract(self):
        error = subprocess.CalledProcessError(1, ["helper"], stderr="缺少必要工作表")
        with patch.dict(os.environ, TOOLBOX_DOMAIN_BACKEND="rust"), \
             patch.object(domain, "native_binary", return_value=Path("helper.exe")), \
             patch.object(domain, "call", side_effect=error):
            with self.assertRaisesRegex(ValueError, "缺少必要工作表"):
                domain.invoke("trading", "summary_parse", {})
