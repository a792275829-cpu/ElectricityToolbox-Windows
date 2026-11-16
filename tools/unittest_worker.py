"""Internal unittest adapter for run_tests.py; persist actual execution/skip counts."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import unittest


class RecordingRunner(unittest.TextTestRunner):
    def run(self, test):
        result = super().run(test)
        Path(os.environ["TOOLBOX_TEST_RESULT"]).write_text(json.dumps({
            "tests": result.testsRun,
            "failures": len(result.failures),
            "errors": len(result.errors),
            "skipped": [{"test": test.id(), "reason": reason} for test, reason in result.skipped],
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        return result


if __name__ == "__main__":
    # Match `python -m unittest`: the selected business directory stays importable.
    sys.path.insert(0, str(Path.cwd()))
    program = unittest.main(module=None, argv=["unittest", *sys.argv[1:]],
                            testRunner=RecordingRunner, exit=False)
    raise SystemExit(not program.result.wasSuccessful())
