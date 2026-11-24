"""Templates keep the layout their readers expect. Offline.

    python3 -m unittest discover -s tests
"""
import re
import unittest
from pathlib import Path

from test_readme import code_spans, commands, link_targets, numbers, prose, sections, table_rows

ROOT = Path(__file__).resolve().parent.parent
NAMING = ROOT / ".cursor" / "skills" / "inferpilot-weekly-naming" / "SKILL.md"
PLACEHOLDER = re.compile(r"<[^<>]*>")
URL = re.compile(r"https://(?:[^\s<>()]|<[^<>]*>)+|(?<=\]\()<[^<>]*>(?=\))")


def urls(text):
    """Every URL and placeholder link target, bare ones and those with spaces too, a placeholder's words as <>."""
    return sorted(PLACEHOLDER.sub("<>", m.group(0)) for m in URL.finditer(text))


class PrBodyTest(unittest.TestCase):
    """templates/pr_body.md is sglang's PR template (.github/pull_request_template.md) with placeholders."""

    body = (ROOT / "templates" / "pr_body.md").read_text(encoding="utf-8")

    def test_sections_in_sglangs_order(self):
        self.assertEqual(re.findall(r"^## (.+)$", self.body, re.M),
                         ["Motivation", "Modifications", "Accuracy Tests", "Speed Tests and Profiling",
                          "Checklist", "Review and Merge Process"])

    def test_sglangs_comments_and_checklist(self):
        self.assertTrue(self.body.startswith("<!-- Thank you for your contribution!"))
        self.assertIn("<!-- PR title: [Type] scope: title, or [Type] [Hardware] scope: title.", self.body)
        items = re.findall(r"^- \[ \] (.+)$", self.body, re.M)
        starts = ["Format your code", "Add unit tests", "Update documentation",
                  "Provide accuracy and speed benchmark results", "Follow the SGLang code style"]
        self.assertEqual(len(items), len(starts))
        for item, start in zip(items, starts):
            self.assertTrue(item.startswith(start), item)


class ScanTemplatesTest(unittest.TestCase):
    """templates/daily_report.md and templates/task_queue.md, which the dispatcher fills."""

    def test_an_effort_cell_says_where_its_numbers_go(self):
        for name in ("daily_report.md", "task_queue.md"):
            text = (ROOT / "templates" / name).read_text(encoding="utf-8")
            units = list(re.finditer(r"\b(?:agent|GPU) h\b", text))
            self.assertTrue(units, name)
            bare = [text[max(0, u.start() - 24):u.end()] for u in units
                    if not re.search(r"<[^<>]+> \Z", text[max(0, u.start() - 60):u.start()])]
            self.assertEqual(bare, [], name)


class WeeklyReleaseTest(unittest.TestCase):
    """templates/weekly_release.md and weekly_release.zh-CN.md: a week of InferPilot's work in the shape of an
    sglang release note, in English and in Chinese, under the headings of the naming skill."""

    body = (ROOT / "templates" / "weekly_release.md").read_text(encoding="utf-8")
    zh = (ROOT / "templates" / "weekly_release.zh-CN.md").read_text(encoding="utf-8")

    def test_an_sglang_release_notes_layout(self):
        self.assertTrue(self.body.startswith("# InferPilot weekly release <start YYYY-MM-DD HH:MM> to <end YYYY-MM-DD HH:MM> (UTC+8)\n"))
        self.assertEqual(re.findall(r"^# (.+)$", self.body, re.M)[1:], ["Highlights", "Full Notes"])
        notes = re.findall(r"^## (.+)$", self.body.split("\n# Full Notes\n", 1)[1], re.M)
        for section in ("Breaking Changes & Upgrade Notes", "Contributors"):
            self.assertIn(section, notes)
        self.assertTrue(self.body.rstrip().splitlines()[-1].startswith("**Full Changelog**: "))

    def test_the_chinese_one_says_the_same(self):
        self.assertTrue(self.zh.startswith("# InferPilot 周报 <起 YYYY-MM-DD HH:MM> 至 <止 YYYY-MM-DD HH:MM>（UTC+8）\n"))
        self.assertTrue(self.zh.rstrip().splitlines()[-1].startswith("**完整变更记录**："))
        en, zh = sections(self.body), sections(self.zh)
        self.assertEqual([level for level, _ in en], [level for level, _ in zh])
        headings = ["(top)"] + re.findall(r"^#{1,6} (.+)$", prose(self.body), re.M)
        for title, (_, a), (_, b) in zip(headings, en, zh):
            with self.subTest(section=title):
                self.assertEqual(code_spans(a), code_spans(b), "inline code only one language has")
                self.assertEqual(numbers(a), numbers(b), "numbers only one language has")
                self.assertEqual(link_targets(a, {}, ""), link_targets(b, {}, ""), "links only one language has")
        self.assertEqual(table_rows(self.body), table_rows(self.zh), "tables with other rows")
        self.assertEqual(commands(self.body), commands(self.zh))
        self.assertEqual(urls(self.body), urls(self.zh), "URLs only one language has, the Full Changelog's too")

    def test_its_headings_are_the_naming_skills(self):
        table = NAMING.read_text(encoding="utf-8").split("## 章节名对照表", 1)[1].split("\n## ", 1)[0]
        rows = [[c.strip() for c in line.strip().strip("|").split("|")] for line in table.splitlines()
                if line.startswith("| ") and not line.startswith("| 英文版")]
        for body, column in ((self.body, 0), (self.zh, 1)):
            self.assertEqual(re.findall(r"^#{1,2} (.+)$", body, re.M)[1:], [row[column] for row in rows],
                             "the template's headings and the naming skill's table differ")


if __name__ == "__main__":
    unittest.main()
