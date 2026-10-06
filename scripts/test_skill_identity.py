"""Regression checks for a standalone, discoverable Project DeepDive package."""

import re
import unittest
from pathlib import Path
from urllib.parse import unquote, urlsplit


PACKAGE_ROOT = Path(__file__).resolve().parents[1]


class SkillIdentityTests(unittest.TestCase):
    def test_package_root_contains_the_canonical_skill_entrypoint(self):
        skill_path = PACKAGE_ROOT / "SKILL.md"
        self.assertTrue(skill_path.is_file())
        frontmatter = skill_path.read_text(encoding="utf-8").split("---", 2)[1]
        self.assertIn("name: project-deepdive", frontmatter.splitlines())

    def test_readme_explains_installation_and_first_use_from_this_package(self):
        readme = (PACKAGE_ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("仓库根本身就是 Skill 包根", readme)
        self.assertIn("git clone https://github.com/zgozh/project-deepdive.git", readme)
        self.assertIn("git@github.com:zgozh/project-deepdive.git", readme)
        self.assertIn("python scripts/skill_selfcheck.py", readme)
        self.assertIn("python scripts/v2_selfcheck.py", readme)
        self.assertIn("docs/使用案例.md", readme)
        for name in ("SKILL.md", "references", "prompts", "scripts", "schemas"):
            with self.subTest(package_content=name):
                self.assertIn(name, readme)
        for link in re.findall(r"\]\(([^)]+)\)", readme):
            target = urlsplit(link).path
            if target:
                with self.subTest(readme_link=link):
                    self.assertTrue((PACKAGE_ROOT / unquote(target)).is_file(), link)

    def test_natural_language_modes_route_to_existing_package_guides(self):
        skill_path = PACKAGE_ROOT / "SKILL.md"
        skill = skill_path.read_text(encoding="utf-8")
        frontmatter = skill.split("---", 2)[1]
        description = frontmatter.split("description:", 1)[1]
        expected_modes = {
            "learn-project", "project-scan", "project-map", "project-book",
            "project-study", "project-interview", "project-extend",
            "project-vibecode", "project-audit", "project-update",
        }
        section = skill.split("## Choose a mode", 1)[1].split("\n## ", 1)[0]
        routes = {}
        for line in section.splitlines():
            if line.startswith("| `"):
                route = line.split("|", 2)[1].strip().strip("`")
                routes[route] = line

        self.assertEqual(expected_modes, set(routes))
        for mode, line in routes.items():
            with self.subTest(mode=mode):
                self.assertIn(mode, description)
                links = re.findall(r"\]\(([^)]+)\)", line)
                self.assertTrue(links, line)
                for link in links:
                    target = urlsplit(link).path
                    if target:
                        self.assertTrue((PACKAGE_ROOT / unquote(target)).is_file(), link)

        mandatory_docs = (
            "references/README.md",
            "references/project-foundation-guide.md",
            "references/coverage-policy.md",
            "references/product-quality-gates.md",
            "references/product-acceptance-criteria.md",
        )
        for relative_path in mandatory_docs:
            with self.subTest(document=relative_path):
                self.assertIn(relative_path, skill)
                self.assertTrue((PACKAGE_ROOT / relative_path).is_file())

        foundation = (PACKAGE_ROOT / "references/project-foundation-guide.md").read_text(
            encoding="utf-8"
        )
        for script in ("scripts/scan_repository.py", "scripts/validate_coverage.py"):
            with self.subTest(script=script):
                self.assertIn(script, foundation)
                self.assertTrue((PACKAGE_ROOT / script).is_file())

    def test_package_entrypoints_reach_the_compatibility_guide_and_manual(self):
        readme = (PACKAGE_ROOT / "README.md").read_text(encoding="utf-8")
        skill = (PACKAGE_ROOT / "SKILL.md").read_text(encoding="utf-8")
        index = (PACKAGE_ROOT / "references" / "README.md").read_text(encoding="utf-8")
        advanced = (PACKAGE_ROOT / "references" / "legacy-and-advanced-workflows.md").read_text(
            encoding="utf-8"
        )
        manual = (PACKAGE_ROOT / "spec" / "操作手册-闸门与工具.md").read_text(encoding="utf-8")

        self.assertIn("legacy-and-advanced-workflows.md", readme)
        self.assertIn("legacy-and-advanced-workflows.md", index)
        self.assertIn("references/README.md", skill)
        for route in ("学习 / 深度拆解 / 开始", "继续 / next", "实验 / 验证机制",
                      "架构挑战 / 重构 / 迁移", "Issue / 功能 / 修复 / 让 AI 改代码",
                      "验收 / 检查完整性"):
            with self.subTest(route=route):
                self.assertIn(route, advanced)
        self.assertIn("update_line", advanced)
        self.assertIn("update_line", manual)

    def test_direct_source_verifier_command_resolves_from_package_root(self):
        guide = (PACKAGE_ROOT / "references" / "direct-source-teaching-guide.md").read_text(
            encoding="utf-8"
        )
        command = "python scripts/verify_reader_source_blocks.py"
        self.assertIn(command, guide)
        self.assertTrue((PACKAGE_ROOT / "scripts" / "verify_reader_source_blocks.py").is_file())

    def test_archived_v2_manual_preserves_historical_warning_and_local_entry(self):
        manual_path = PACKAGE_ROOT / "docs" / "archive" / "V2试运行手册.md"
        manual = manual_path.read_text(encoding="utf-8")
        notice = manual.split("## 目标", 1)[0]

        self.assertIn("历史资料", notice)
        self.assertIn("不可作为当前安装指令", notice)
        self.assertIn("skills/project-deepdive/", notice)
        self.assertIn("[SKILL.md](../../SKILL.md)", notice)
        self.assertTrue((manual_path.parent / "../../SKILL.md").resolve().is_file())
        self.assertIn("运行 `replicate-learning`", manual)


if __name__ == "__main__":
    unittest.main()
