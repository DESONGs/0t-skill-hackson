from __future__ import annotations

from pathlib import Path
import os
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
VERIFY_SCRIPT = REPO_ROOT / "scripts" / "verify.sh"


class VerifyScriptTests(unittest.TestCase):
    def test_verify_script_exists_and_is_executable(self) -> None:
        self.assertTrue(VERIFY_SCRIPT.is_file(), "verify.sh should exist")
        self.assertTrue(os.access(VERIFY_SCRIPT, os.X_OK), "verify.sh should be executable")

    def test_verify_script_includes_required_regression_entrypoints(self) -> None:
        content = VERIFY_SCRIPT.read_text(encoding="utf-8")
        self.assertIn("test_style_distillation_archetype.py", content)
        self.assertIn("test_style_distillation_archetype_integration.py", content)
        self.assertIn("test_wallet_style_reflection.py", content)
        self.assertIn("test_qa_evaluator_status_semantics.py", content)


if __name__ == "__main__":
    unittest.main()
