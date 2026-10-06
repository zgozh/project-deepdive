"""Tests for the package script discoverability boundary."""

import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
from skill_selfcheck import analyze_script_discoverability  # noqa: E402


class ScriptDiscoverabilityTests(unittest.TestCase):
    def test_documented_entry_reaches_imported_support_and_reports_missing_orphan(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            scripts = root / "scripts"
            scripts.mkdir()
            (scripts / "entry.py").write_text("from support import run\n", encoding="utf-8")
            (scripts / "support.py").write_text("def run():\n    return None\n", encoding="utf-8")
            (scripts / "api.py").write_text("def run():\n    return None\n", encoding="utf-8")
            (scripts / "orphan.py").write_text("VALUE = 1\n", encoding="utf-8")

            result = analyze_script_discoverability(
                root,
                {
                    "README.md": (
                        "Run `scripts/entry.py` and `api.py`; missing `scripts/absent.py`.\n"
                        "```python\nfrom api import run\n```"
                    ),
                },
            )

        self.assertEqual(["api", "entry"], result["entrypoints"])
        self.assertEqual(["api", "entry", "support"], result["reachable"])
        self.assertEqual(["absent.py"], result["missing"])
        self.assertEqual(["orphan"], result["orphans"])
        self.assertEqual([], result["parse_errors"])


if __name__ == "__main__":
    unittest.main()
