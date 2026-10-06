"""Keep the executable skill entry short and the book scope unambiguous."""

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class SkillContractTests(unittest.TestCase):
    def test_entry_is_short_and_points_to_preserved_detail(self):
        skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        self.assertLessEqual(len(skill.splitlines()), 220)
        detailed_guides = (
            "references/批次讲解全文模板.md",
            "references/phase6-source-topic-writing-card.md",
        )
        for relative_path in detailed_guides:
            with self.subTest(guide=relative_path):
                self.assertIn(relative_path, skill)
                self.assertTrue((ROOT / relative_path).is_file())

    def test_default_learning_scope_is_bounded_and_additional_books_are_opt_in(self):
        skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        routes = skill.split("## Choose a mode", 1)[1].split("\n## ", 1)[0]
        learn_route = next(line for line in routes.splitlines() if "`learn-project`" in line)
        book_route = next(line for line in routes.splitlines() if "`project-book`" in line)
        optional_workflows = skill.split("## Optional and compatible workflows", 1)[1].split(
            "\n## ", 1
        )[0]

        self.assertIn("bounded", learn_route.lower())
        self.assertIn("first", learn_route.lower())
        self.assertIn("book", learn_route.lower())
        self.assertIn("chapter", book_route.lower())
        self.assertIn("user actually requests", book_route.lower())
        self.assertIn("answer books", optional_workflows.lower())
        self.assertIn("opt-in", optional_workflows.lower())

    def test_v2_contract_requires_optional_books_only_when_requested(self):
        contract = json.loads((ROOT / "spec/V2质量契约.json").read_text(encoding="utf-8"))
        entry = next(item for item in contract["entries"] if item["id"] == "V2-02")
        self.assertIn("显式", entry["stmt"])


if __name__ == "__main__":
    unittest.main()
