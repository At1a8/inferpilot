"""Templates keep the layout their readers expect. Offline.

    python3 -m unittest discover -s tests
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


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
            units = re.findall(r"(<[^<>]+>|\S+) (?:agent|GPU) h\b", text)
            self.assertTrue(units, name)
            self.assertEqual([u for u in units if not re.fullmatch(r"<[^<>]+>", u)], [], name)


if __name__ == "__main__":
    unittest.main()
