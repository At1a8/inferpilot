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
ATTRIBUTION = ("Developed with the [InferPilot](https://github.com/At1a8/inferpilot) AI agent; "
               "reviewed and measured by AMD engineers.")


def urls(text):
    """Every URL and placeholder link target, bare ones and those with spaces too, a placeholder's words as <>."""
    return sorted(PLACEHOLDER.sub("<>", m.group(0)) for m in URL.finditer(text))


class PrBodyTest(unittest.TestCase):
    """templates/pr_body.md is sglang's PR template (.github/pull_request_template.md) with placeholders and
    InferPilot's attribution line."""

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

    def test_attribution_line_after_the_checklist(self):
        self.assertEqual(self.body.count(ATTRIBUTION), 1)
        line = self.body.index(ATTRIBUTION)
        self.assertLess(self.body.index("## Checklist"), line)
        self.assertLess(line, self.body.index("## Review and Merge Process"))


class AttributionTest(unittest.TestCase):
    """Every file that gives the attribution line gives it word for word: one wording is what a search of
    upstream PR bodies finds."""

    def test_one_wording(self):
        for name in ("templates/pr_body.md", "knowledge/pr_playbook.md", "README.md", "README.zh-CN.md"):
            text = (ROOT / name).read_text(encoding="utf-8")
            self.assertIn(ATTRIBUTION, text, name)
            self.assertEqual(text.count("Developed with the [InferPilot]"), text.count(ATTRIBUTION), name)


def items(text):
    """Paragraphs, list items and table rows, each on one line."""
    return [" ".join(part.split()) for part in re.split(r"\n(?=[ \t]*(?:\d+\.|[-*]) |\|)|\n[ \t]*\n", text)]


def about_the_metric(name, phase=None):
    """What a file, or one phase of a role, says of the feature's own metric outside its tables, as one line."""
    text = (ROOT / name).read_text(encoding="utf-8")
    if phase:
        text = text.split(phase, 1)[1].split("\n## ", 1)[0]
    return " ".join(i for i in items(text) if "the feature's own metric" in i.lower() and not i.startswith("|"))


class FeatureMetricTest(unittest.TestCase):
    """templates/feature_spec.md asks a port for its feature's own metric (accept length for speculative
    decoding) in G1, against the source's value, when there is a value to compare with. The doctrine and
    the roles that write the spec, plan the work package, write its test, run it and verify the branch say
    the same: where it is judged, on whose prompts, that the unit gate runs the measurement whatever command
    it is given, and that every Verify round measures it again on the commit it verifies."""

    def test_the_spec_template_asks_for_it_in_g1(self):
        spec = (ROOT / "templates" / "feature_spec.md").read_text(encoding="utf-8")
        rows = [line for line in spec.splitlines() if line.startswith("| G1 ")]
        self.assertEqual(len(rows), 1, "the acceptance criteria have one G1 row")
        for said in ("the feature's own metric", "<tolerance>", "the source's", "with a value to compare with"):
            self.assertIn(said, rows[0])

    def test_the_doctrine_and_the_roles_judge_it_in_g1_on_the_sources_prompts(self):
        for name, phase in (("knowledge/validation_doctrine.md", None), ("roles/director.md", "## PHASE=spec"),
                            ("roles/architect.md", "## PHASE=design"), ("roles/test_engineer.md", "## PHASE=unit"),
                            ("roles/verify_engineer.md", "## PHASE=verify")):
            where = about_the_metric(name, phase)
            self.assertTrue(where, "%s does not name the feature's own metric" % name)
            self.assertIn("G1", where, "%s does not say the feature's own metric is judged in G1" % name)
            self.assertRegex(where, r"the source's(?: own)? prompt", "%s does not say on whose prompts it is measured" % name)

    def test_without_a_value_to_compare_with_it_is_reported_and_no_criterion(self):
        for name in ("knowledge/validation_doctrine.md", "roles/director.md", "templates/feature_spec.md"):
            self.assertIn("reported", about_the_metric(name), "%s does not say what a port with no value to compare with does" % name)
        for name in ("roles/architect.md", "knowledge/dev_methodology.md"):
            text = " ".join((ROOT / name).read_text(encoding="utf-8").split())
            self.assertIn("spec gives the feature's own metric a tolerance", text, "%s asks every port for the comparison" % name)

    def test_the_unit_gate_runs_the_measurement_whatever_command_it_is_given(self):
        tester = about_the_metric("roles/test_engineer.md", "## PHASE=unit")
        for named in ("`test`", "`unit_test_cmd`", "`unit_pass`"):
            self.assertIn(named, tester, "roles/test_engineer.md: a narrower command could close the package")
        self.assertIn("`unit_test_cmd`", about_the_metric("roles/implementer.md", "## PHASE=implement"),
                      "roles/implementer.md does not say its command is the measurement")

    def test_every_verify_round_measures_it_again_on_the_commit_it_verifies(self):
        verify = about_the_metric("roles/verify_engineer.md", "## PHASE=verify")
        for said in ("again", "blocking", "commit", "`gates`"):
            self.assertIn(said, verify, "roles/verify_engineer.md: a package's pass could stand for a later commit")
        self.assertRegex(about_the_metric("knowledge/validation_doctrine.md"), r"every Verify round measures the metric again",
                         "knowledge/validation_doctrine.md does not say the pass holds for its commit only")


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

    def test_the_long_threshold_is_the_same_where_the_ranking_is_written(self):
        """dispatch.md §6 orders a rank by effort, says what a GPU hour is and names when a task is long; the
        dispatcher, the queue template and inferpilot.js's LONG_GPU_H say the same number of GPU hours."""
        rank = (ROOT / "knowledge" / "dispatch.md").read_text(encoding="utf-8").split("## 6. Ranking", 1)[1]
        self.assertIn("then the smaller", rank)
        self.assertIn("not hours times GPUs", re.sub(r"\s+", " ", rank))
        found = {}
        for name, path in (("dispatch.md", "knowledge/dispatch.md"), ("dispatcher.md", "roles/dispatcher.md"),
                           ("task_queue.md", "templates/task_queue.md")):
            text = re.sub(r"\s+", " ", (ROOT / path).read_text(encoding="utf-8"))
            found[name] = set(re.findall(r"(\d+) GPU hours or more", text))
            self.assertTrue(found[name], "%s does not say when a task is long" % name)
        found["inferpilot.js"] = set(re.findall(r"^const LONG_GPU_H = (\d+);", (ROOT / "inferpilot.js").read_text(
            encoding="utf-8"), re.M))
        self.assertTrue(found["inferpilot.js"], "inferpilot.js has no LONG_GPU_H")
        self.assertEqual(len(set.union(*found.values())), 1, found)


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
                if line.startswith("| ") and not line.startswith("| 章节名")]
        for body in (self.body, self.zh):
            self.assertEqual(re.findall(r"^#{1,2} (.+)$", body, re.M)[1:], [row[0] for row in rows],
                             "the template's headings and the naming skill's table differ: both use its English names")


if __name__ == "__main__":
    unittest.main()
