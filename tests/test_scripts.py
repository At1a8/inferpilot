"""Offline unit tests for the scan and audit scripts (no network, no GPU).

    python3 -m unittest discover -s tests -p 'test_*.py'
"""
import argparse
import io
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

import audit_host_syncs  # noqa: E402
import audit_platform_gates  # noqa: E402
import baseline_cache  # noqa: E402
import check_framework_skills  # noqa: E402
import latest_image  # noqa: E402
import daily_report  # noqa: E402
import leak_scan  # noqa: E402
import find_skill  # noqa: E402
import gpu_status  # noqa: E402
import harness  # noqa: E402
import propose_knowledge  # noqa: E402
import run_lock  # noqa: E402
import scan_ci  # noqa: E402
import scan_issues  # noqa: E402
import scan_upstream  # noqa: E402
import share_daily_report  # noqa: E402
import weekly_report  # noqa: E402


class CheckFrameworkSkillsTest(unittest.TestCase):
    INDEX = textwrap.dedent("""\
        | Role | Phase | Skills | When to read |
        |---|---|---|---|
        | implementer | implement | `write-sglang-test`, `gone-skill` | a `not-a-skill` flag in this column is ignored |
        | investigator | investigate | `debug-cuda-crash` | crashes |

        Not used by InferPilot: `cookbook-migrate-model`.
        """)

    def test_drift(self):
        with tempfile.TemporaryDirectory() as d:
            for rel in (".agents/skills/write-sglang-test", ".agents/skills/debug-cuda-crash",
                        ".agents/skills/cookbook-migrate-model", ".agents/skills/new-skill",
                        "python/sglang/multimodal_gen/.agents/skills/sglang-diffusion-add-model"):
                Path(d, rel).mkdir(parents=True)
                Path(d, rel, "SKILL.md").write_text("---\nname: x\n---\n")
            Path(d, ".agents/skills/no-skill-md").mkdir(parents=True)
            self.assertEqual(check_framework_skills.table_names(self.INDEX),
                             ["debug-cuda-crash", "gone-skill", "write-sglang-test"])
            r = check_framework_skills.check(self.INDEX, d)
            self.assertEqual(r["missing"], ["gone-skill"])
            self.assertEqual(r["unindexed"], ["new-skill", "sglang-diffusion-add-model"])
            self.assertEqual(r["present"], 5)


class FindSkillTest(unittest.TestCase):
    INDEX = textwrap.dedent("""\
        # comment
        schema: {id, file, tags, match, depends_on, validation_status}

        skills:
        - id: fly-dspark
          file: fly-dspark/SKILL.md
          tags: [fly, speculative decoding, dspark]
          match:
            framework: sglang
            feature: fly_speculative_verification
            model_arch: [deepseek_v4]      # DeepSeek-V4.1
            platforms: [rocm-gfx950]
          depends_on: [vllm-project/vllm#53987]
          validation_status: draft
        - id: hisparse-v2
          file: hisparse-v2/SKILL.md
          tags: [hisparse, sparse attention, speculative decoding]
          match:
            feature: hisparse_v2
            model_arch: [deepseek_v4]
          validation_status: validated
        """)

    def find(self, query):
        with tempfile.TemporaryDirectory() as d:
            index = Path(d, "index.yaml")
            index.write_text(self.INDEX)
            Path(d, "fly-dspark").mkdir()
            Path(d, "fly-dspark", "SKILL.md").write_text('---\nid: fly-dspark\ntitle: "FLy on DSPARK"\n---\n')
            return find_skill.find(query, index)

    def test_parse(self):
        with tempfile.TemporaryDirectory() as d:
            index = Path(d, "index.yaml")
            index.write_text(self.INDEX)
            fly = find_skill.load_index(index)[0]
        self.assertEqual(fly["tags"], ["fly", "speculative decoding", "dspark"])
        self.assertEqual(fly["feature"], "fly_speculative_verification")
        self.assertEqual(fly["model_arch"], ["deepseek_v4"])
        self.assertEqual(fly["depends_on"], ["vllm-project/vllm#53987"])

    def test_tags_id_and_feature_find_a_skill(self):
        for query in ("FLy", "DSPARK verify", "bring fly_speculative_verification to AMD", "Fly-DSpark"):
            with self.subTest(query=query):
                found = self.find(query)
                self.assertEqual([s["id"] for s in found], ["fly-dspark"])
                self.assertEqual(found[0]["title"], "FLy on DSPARK")
        self.assertEqual([s["id"] for s in self.find("HiSparse_V2 port")], ["hisparse-v2"])

    def test_whole_words_and_no_match_on_model_alone(self):
        self.assertEqual(self.find("a flywheel for sparse attentions"), [])
        self.assertEqual(self.find("DeepSeek-V4.1 decode is slow on rocm-gfx950"), [])

    def test_order(self):
        # Both have one feature hit; the model breaks no tie here, the status does.
        self.assertEqual([s["id"] for s in self.find("speculative decoding")], ["hisparse-v2", "fly-dspark"])
        found = self.find("speculative decoding with FLy for DeepSeek-V4")
        self.assertEqual([s["id"] for s in found], ["fly-dspark", "hisparse-v2"])
        self.assertEqual(found[0]["matched"], ["fly", "speculative decoding", "deepseek_v4"])

    def test_command_line(self):
        with tempfile.TemporaryDirectory() as d:
            index = Path(d, "index.yaml")
            index.write_text(self.INDEX)
            out = io.StringIO()
            with redirect_stdout(out):
                self.assertEqual(find_skill.main(["--index", str(index), "--json", "DSPARK"]), 0)
            self.assertEqual([s["id"] for s in json.loads(out.getvalue())], ["fly-dspark"])
            out = io.StringIO()
            with redirect_stdout(out):
                find_skill.main(["--index", str(index), "nothing", "here"])
            self.assertIn("no feature skill matches", out.getvalue())

    def test_the_real_index(self):
        skills = find_skill.load_index(find_skill.INDEX)
        self.assertTrue(skills)
        for entry in skills:
            with self.subTest(skill=entry["id"]):
                self.assertTrue(entry.get("tags"), "every skill needs tags")
                front = (find_skill.INDEX.parent / entry["file"]).read_text(encoding="utf-8").split("\n---", 1)[0]
                tags = re.search(r"^tags: \[(.*)\]", front, re.M)
                self.assertIsNotNone(tags, "the SKILL.md frontmatter needs the same tags")
                self.assertEqual([t.strip() for t in tags[1].split(",")], entry["tags"])
        self.assertIn("hisparse-v2", [s["id"] for s in find_skill.find("sparse attention")])


class LatestImageTest(unittest.TestCase):
    TAGS = [  # as Docker Hub lists them, newest push first
        {"name": "v0.5.23-rocm720-mi30x-20261104", "last_updated": "2026-11-04T12:40:00Z"},
        {"name": "miles-rocm10-mi35x", "last_updated": "2026-11-04T11:31:00Z"},
        {"name": "miles-rocm10-mi35x-20261104", "last_updated": "2026-11-04T11:30:58Z"},
        {"name": "v0.5.23-rocm10-mi35x-20261103", "last_updated": "2026-11-03T12:00:52Z"},
        {"name": "v0.5.23-rocm10-mi30x-20261103", "last_updated": "2026-11-03T11:44:58Z"},
        {"name": "v0.5.22-rocm10-mi35x-20261021", "last_updated": "2026-11-03T09:00:00Z"},  # re-pushed
        {"name": "v0.5.22.post1-rocm10-mi35x-20261103", "last_updated": "2026-11-03T08:00:00Z"},
        {"name": "v0.5.23-rocm10-mi35x-20261103-debug", "last_updated": "2026-11-03T13:00:00Z"},
    ]

    def test_newest_daily_build_of_the_variant(self):
        self.assertEqual(latest_image.pick(self.TAGS, "rocm10-mi35x"), "v0.5.23-rocm10-mi35x-20261103")
        self.assertEqual(latest_image.pick(self.TAGS, "rocm10-mi30x"), "v0.5.23-rocm10-mi30x-20261103")
        self.assertEqual(latest_image.pick(self.TAGS, "rocm720-mi30x"), "v0.5.23-rocm720-mi30x-20261104")

    def test_miles_and_other_tags_never_count(self):
        miles = [t for t in self.TAGS if t["name"].startswith("miles")]
        self.assertEqual(latest_image.pick(miles, "rocm10-mi35x"), "")
        self.assertIsNone(latest_image.daily_key("v0.5.23-rocm10-mi35x-20261103-debug", "rocm10-mi35x"))
        self.assertIsNone(latest_image.daily_key("v0.5.23-rocm10-mi35xx-20261103", "rocm10-mi35x"))
        self.assertEqual(latest_image.daily_key("v0.5.22.post1-rocm10-mi35x-20261103", "rocm10-mi35x"),
                         ("20261103", (0, 5, 22)))

    def test_compare_with_the_newest_of_the_same_variant(self):
        self.assertEqual(latest_image.parse_daily("rocm/sgl-dev:v0.5.23-rocm10-mi35x-20261102"),
                         ("rocm/sgl-dev", "rocm10-mi35x", "20261102"))
        self.assertIsNone(latest_image.parse_daily("rocm/sgl-dev:miles-rocm10-mi35x-20261104"))
        self.assertIsNone(latest_image.parse_daily("my/custom:latest"))
        cmp = latest_image.compare
        self.assertEqual(cmp("rocm/sgl-dev:v0.5.23-rocm10-mi35x-20261102", self.TAGS), "older rocm/sgl-dev:v0.5.23-rocm10-mi35x-20261103")
        self.assertEqual(cmp("rocm/sgl-dev:v0.5.23-rocm10-mi35x-20261103", self.TAGS), "current")
        self.assertEqual(cmp("rocm/sgl-dev:v0.5.23-rocm720-mi30x-20261104", self.TAGS), "current", "a variant against its own newest")
        self.assertEqual(cmp("rocm/sgl-dev:v0.5.23-rocm10-mi35x-20261102", None), "unknown", "Docker Hub not readable")
        self.assertEqual(cmp("my/custom:latest", self.TAGS), "not-daily")


class WeeklyReportTest(unittest.TestCase):
    """scripts/weekly_report.py: the range and what the weekly report counts; GitHub and git are mocked."""

    NOW = weekly_report.dt.datetime(2026, 11, 24, 9, 57, tzinfo=weekly_report.UTC8)

    def test_by_default_from_seven_days_back_at_midnight_to_now(self):
        start, end = weekly_report.window(now=self.NOW)
        self.assertEqual((start.isoformat(), end), ("2026-11-17T00:00:00+08:00", self.NOW))

    def test_a_range_given_as_dates_or_times_in_utc8(self):
        start, end = weekly_report.window(now=self.NOW, since="2026-11-17", until="2026-11-23")
        self.assertEqual((start.isoformat(), end.isoformat()), ("2026-11-17T00:00:00+08:00", "2026-11-24T00:00:00+08:00"))
        start, end = weekly_report.window(now=self.NOW, since="2026-11-17 08:30", until="2026-11-24T10:00Z")
        self.assertEqual((start.isoformat(), end.isoformat()), ("2026-11-17T08:30:00+08:00", "2026-11-24T18:00:00+08:00"))

    def test_an_end_alone_takes_the_seven_days_before_it(self):
        start, end = weekly_report.window(now=self.NOW, until="2026-11-16")
        self.assertEqual((start.isoformat(), end.isoformat()), ("2026-11-10T00:00:00+08:00", "2026-11-17T00:00:00+08:00"))

    def test_an_empty_range_is_refused(self):
        with self.assertRaises(ValueError):
            weekly_report.window(now=self.NOW, since="2026-11-24 10:00")
        with redirect_stderr(io.StringIO()) as err:
            self.assertEqual(weekly_report.main(["--since", "2026-11-24", "--until", "2026-11-20"]), 2)
        self.assertIn("empty", err.getvalue())

    @staticmethod
    def pull(n, created, merged=None, closed=None, head="b", title="t", state=None):
        return {"number": n, "title": title, "user": {"login": "u"}, "html_url": "h%d" % n, "head": {"ref": head},
                "created_at": created, "merged_at": merged, "closed_at": closed or merged,
                "state": state or ("closed" if closed or merged else "open")}

    @staticmethod
    def events(**by_number):
        """Issue events by number: n7=[("closed", "2026-11-20T00:00:00Z"), ...]."""
        table = {int(k[1:]): [{"event": e, "created_at": t} for e, t in v] for k, v in by_number.items()}
        return lambda n: table.get(n, [])

    def test_a_pull_request_s_state_is_its_state_at_the_range_s_end(self):
        start, end = weekly_report.window(now=self.NOW)
        pulls = [self.pull(1, "2026-11-10T00:00:00Z", merged="2026-11-16T15:59:59Z"),  # 23:59:59 UTC+8, the day before
                 self.pull(2, "2026-11-10T00:00:00Z", merged="2026-11-16T16:00:00Z"),  # 00:00 UTC+8, the first second
                 self.pull(3, "2026-11-18T00:00:00Z", closed="2026-11-19T00:00:00Z"),
                 self.pull(4, "2026-11-23T00:00:00Z"),
                 self.pull(5, "2026-11-12T00:00:00Z", closed="2026-11-24T02:00:00Z"),  # closed after the end
                 self.pull(6, "2026-11-24T02:00:00Z"),                                 # opened after the end
                 self.pull(7, "2026-11-12T00:00:00Z"),                                 # closed in the range, reopened after
                 self.pull(8, "2026-11-12T00:00:00Z"),                                 # closed and reopened in the range
                 self.pull(9, "2026-11-12T00:00:00Z", merged="2026-11-24T03:00:00Z")]  # merged after the end
        events = self.events(n7=[("closed", "2026-11-20T00:00:00Z"), ("reopened", "2026-11-24T03:00:00Z")],
                             n8=[("closed", "2026-11-19T00:00:00Z"), ("reopened", "2026-11-21T00:00:00Z")])
        got = weekly_report.sort_pulls(pulls, start, end, events)
        self.assertEqual([[p["number"] for p in got[k]] for k in ("merged", "closed", "open")], [[2], [3, 7], [4, 5, 8, 9]])

    def test_main_at_a_time_along_its_first_parent_line_with_git_else_from_github(self):
        start, _ = weekly_report.window(now=self.NOW)
        calls = []
        with mock.patch.object(weekly_report, "git", side_effect=lambda *a: calls.append(a) or
                               subprocess.CompletedProcess(a, 0, "abc123\n", "")):
            self.assertEqual(weekly_report.main_at("o/r", start, True), ("abc123", "git"))
        self.assertEqual(calls, [("rev-list", "-1", "--first-parent", "--before=2026-11-17 00:00:00 +0800", "origin/main")])
        with mock.patch.object(weekly_report, "git", return_value=subprocess.CompletedProcess((), 128, "", "bad")), \
                mock.patch.object(weekly_report._github, "get", return_value=[{"sha": "def456"}]) as get:
            self.assertEqual(weekly_report.main_at("o/r", start, True), ("def456", "github"))
        self.assertEqual(get.call_args[0][1]["until"], "2026-11-16T16:00:00Z")

    def test_numstat_reads_renames_and_binary_files(self):
        self.assertEqual(weekly_report.numstat("5\t2\tREADME.md\0-\t-\timg.png\0" "3\t1\t\0old.py\0new.py\0"),
                         [(5, 2, "README.md"), (0, 0, "img.png"), (3, 1, "new.py")])

    def test_git_runs_under_the_c_locale(self):
        with mock.patch.object(weekly_report.subprocess, "run", return_value=subprocess.CompletedProcess((), 0, "", "")) as run:
            weekly_report.git("diff", "--numstat", "-z", "a", "b")
        self.assertEqual(run.call_args.kwargs["env"]["LC_ALL"], "C")

    def test_the_change_from_git_and_from_github_only_when_git_cannot(self):
        with mock.patch.object(weekly_report, "git", return_value=subprocess.CompletedProcess((), 0, "40\t1\ta.py\0" "3\t1\tb.md\0", "")):
            got = weekly_report.change("o/r", "a1", "b2", True)
        self.assertEqual(got, {"files": 2, "insertions": 43, "deletions": 2, "counted_by": "git", "complete": True,
                               "paths": ["a.py", "b.md"]})
        many = {"files": [{"filename": "f%d" % i, "additions": 1, "deletions": 0} for i in range(300)]}
        for use_git, files, complete in ((False, many, False), (True, {"files": many["files"][:2]}, True)):
            with mock.patch.object(weekly_report, "git", return_value=subprocess.CompletedProcess((), 128, "", "")), \
                    mock.patch.object(weekly_report._github, "get", return_value=files):
                got = weekly_report.change("o/r", "a1", "b2", use_git)
            self.assertEqual((got["counted_by"], got["complete"], got["files"]), ("github", complete, len(files["files"])))

    def test_git_answers_only_in_a_checkout_of_the_repository(self):
        for url, ready in (("https://github.com/At1a8/inferpilot.git", True), ("git@github.com:at1a8/InferPilot.git", True),
                           ("https://github.com/At1a8/inferpilot-old.git", False), ("https://github.com/notAt1a8/inferpilot", False)):
            with mock.patch.object(weekly_report, "git", side_effect=lambda *a, u=url: subprocess.CompletedProcess(
                    a, 0, u + "\n" if a[0] == "remote" else "", "")):
                self.assertEqual(weekly_report.git_ready("At1a8/inferpilot"), ready, url)

    def test_a_range_from_before_the_first_commit_counts_from_an_empty_tree(self):
        calls = []
        with mock.patch.object(weekly_report, "git", side_effect=lambda *a: calls.append(a) or
                               subprocess.CompletedProcess(a, 0, "7\t0\tREADME.md\0", "")):
            got = weekly_report.change("o/r", "", "b2", True)
        self.assertEqual((got["files"], got["insertions"], got["complete"], calls[0][3]), (1, 7, True, weekly_report.EMPTY_TREE))
        got = weekly_report.change("o/r", "", "b2", False)
        self.assertEqual((got["counted_by"], got["complete"]), ("", False))
        self.assertTrue(weekly_report.change("o/r", "", "", True)["complete"])

    def test_ports_recorded_by_every_feature_skill_of_the_range_s_end(self):
        files = {"knowledge/feature_skills/pard2/SKILL.md": 'id: pard2\nreference_pr: "sgl-project/sglang#51465"\n',
                 "knowledge/feature_skills/other/SKILL.md": 'reference_pr: "vllm-project/vllm#123"\n',
                 "knowledge/feature_skills/_template/SKILL_TEMPLATE.md": 'reference_pr: "sgl-project/sglang#1"\n',
                 "knowledge/feature_skills/README.md": ""}

        def git(*a):
            if a[0] == "ls-tree":
                return subprocess.CompletedProcess(a, 0, "\n".join(files) + "\n", "")
            return subprocess.CompletedProcess(a, 0, files[a[1].split(":", 1)[1]], "")
        with mock.patch.object(weekly_report, "git", side_effect=git):
            self.assertEqual(weekly_report.recorded_ports("o/r", "sgl-project/sglang", "b2", True), {51465: "feature_skill:pard2"})
            self.assertEqual(weekly_report.recorded_ports("o/r", "vllm-project/vllm", "b2", True), {123: "feature_skill:other"})

    def test_no_skills_directory_means_no_recorded_ports(self):
        with mock.patch.object(weekly_report, "git", return_value=subprocess.CompletedProcess((), 0, "", "")), \
                mock.patch.object(weekly_report._github, "get") as get:
            self.assertEqual(weekly_report.recorded_ports("o/r", "o/s", "b2", True), {})
        get.assert_not_called()
        with mock.patch.object(weekly_report._github, "get",
                               side_effect=weekly_report.urllib.error.HTTPError("u", 404, "Not Found", {}, None)):
            self.assertEqual(weekly_report.recorded_ports("o/r", "o/s", "b2", False), {})

    def test_upstream_pulls_active_in_the_range_and_open_at_its_end(self):
        start, end = weekly_report.window(now=self.NOW)
        prs = {1: self.pull(1, "2026-11-01T00:00:00Z", head="inferpilot/50386-x"),
               2: self.pull(2, "2026-11-01T00:00:00Z", head="someone/x"),
               3: self.pull(3, "2026-11-01T00:00:00Z", head="inferpilot/9-idle"),
               4: self.pull(4, "2026-11-19T00:00:00Z", head="fangyuan/pard2", title="[Feat] [AMD] PARD2"),
               5: self.pull(5, "2026-11-18T00:00:00Z", head="inferpilot/7-late", merged="2026-11-25T00:00:00Z"),
               6: self.pull(6, "2026-11-01T00:00:00Z", head="inferpilot/8-reopened")}
        commits = {1: ["2026-11-20T00:00:00Z"], 3: ["2026-11-02T00:00:00Z"]}
        events = {6: [("closed", "2026-11-19T00:00:00Z"), ("reopened", "2026-11-20T00:00:00Z")]}

        def paginate(path, params=None, max_pages=20):
            n = int(path.split("/")[-2])
            if path.endswith("/commits"):
                return [{"commit": {"committer": {"date": d}}} for d in commits.get(n, [])]
            return [{"event": e, "created_at": t} for e, t in events.get(n, [])]
        with mock.patch.object(weekly_report._github, "search_issues", return_value=[{"number": n} for n in (1, 2, 3, 5, 6)]), \
                mock.patch.object(weekly_report._github, "get", side_effect=lambda path, params=None: prs[int(path.rsplit("/", 1)[1])]), \
                mock.patch.object(weekly_report._github, "paginate", side_effect=paginate):
            got = weekly_report.upstream_pulls("o/s", start, end, {4: "feature_skill:pard2"})
        self.assertEqual([(p["number"], p["state"], p["kind_hint"], p["source"]) for p in got["pulls"]],
                         [(1, "open", "issue", "branch"), (4, "open", "feature", "feature_skill:pard2"),
                          (5, "open", "issue", "branch"), (6, "open", "issue", "branch")])
        self.assertEqual([p["number"] for p in got["open_at_end"]], [1, 3, 4, 5, 6])

    def test_every_inferpilot_pull_request_is_read(self):
        start, end = weekly_report.window(now=self.NOW)
        with mock.patch.object(weekly_report, "git_ready", return_value=False), \
                mock.patch.object(weekly_report._github, "paginate", return_value=[]) as paginate, \
                mock.patch.object(weekly_report, "main_at", return_value=("", "github")), \
                mock.patch.object(weekly_report, "upstream_pulls", return_value={"pulls": [], "open_at_end": []}):
            weekly_report.collect("o/r", "o/s", start, end)
        args, kwargs = paginate.call_args_list[0]
        self.assertEqual((args[0], kwargs["max_pages"] >= 10000), ("/repos/o/r/pulls", True))

    def test_a_kind_from_the_branch_or_the_title(self):
        k = weekly_report.kind_hint
        self.assertEqual([k("inferpilot/133537752860-qwen35-encoder-sdpa", "[Fix] [AMD] x"),
                          k("inferpilot/50386-int8-w8a8-rocm", "[Fix] [AMD] x"), k("fangyuan/pard2", "[Feat] [AMD] PARD2: x"),
                          k("inferpilot/x", "[Perf] y"), k("inferpilot/gfx942-mla-fp8-ps0-startup", "[Fix] [AMD] aiter: x")],
                         ["ci", "issue", "feature", "perf", ""])

    def test_a_github_failure_exits_1(self):
        with mock.patch.object(weekly_report, "collect", side_effect=weekly_report.urllib.error.URLError("down")), \
                redirect_stderr(io.StringIO()) as err:
            self.assertEqual(weekly_report.main([]), 1)
        self.assertIn("down", err.getvalue())


class DailyReportTest(unittest.TestCase):
    """The scan's daily report: checked before it is shared, found again once shared. Read only."""

    def issue(self, number, date, created="T01:00:00Z", marker=True):
        return {"number": number, "title": daily_report.title(date), "state": "open", "created_at": date + created,
                "html_url": "https://github.com/At1a8/inferpilot/issues/%d" % number,
                "body": ("q\n" + daily_report.MARKER) if marker else "unrelated"}

    def test_repo_from_url(self):
        for url in ("https://github.com/At1a8/inferpilot.git", "git@github.com:At1a8/inferpilot.git", "https://github.com/At1a8/inferpilot"):
            self.assertEqual(daily_report.repo_from_url(url), "At1a8/inferpilot")
        self.assertEqual(daily_report.repo_from_url("https://gitlab.com/a/b.git"), "")

    def test_a_private_detail_stops_the_report(self):
        body = "| 1 | fix X | see rocm.docs.amd.com |\nlocal models `/data/models/some-model`\nhost gpu-node-7\nsee confluence"
        self.assertEqual([l.split(":")[0] for l in daily_report.leaks(body, "gpu-node-7")], ["2", "3", "4"])
        self.assertEqual(daily_report.leaks("| 1 | GLM crashes with --dcp-size 8 | #41299 |", "gpu-node-7"), [])

    def test_a_sibling_machine_and_a_storage_root_stop_the_report(self):
        body = "ran on gpu-node-8\nweights in /models/x\nok"
        self.assertEqual([l.split(":")[0] for l in daily_report.leaks(body, "gpu-node-7")], ["1", "2"])

    def test_a_pull_request_or_issue_by_its_number_alone_stops_the_report(self):
        body = "\n".join([
            "| [#48414](https://github.com/sgl-project/sglang/pull/48414) | 2026-11-16 | new CUDA-only gate |",
            "| [#46784](https://github.com/sgl-project/sglang/pull/46784) [Feat] DSA MTP draft prefill | 2026-11-22 |",
            "| [#41299](https://github.com/sgl-project/sglang/issues/41299) *new* | Qwen3.8-Flash-Next FP8, gfx950 |",
            "- [#41852](https://github.com/sgl-project/sglang/issues/41852): feature request (plan)",
            "- [#38710](https://github.com/sgl-project/sglang/issues/38710) [Bug] auto-enable commented out *new*: close",
            "| 6 | Kimi-K3.1 Day-0 branch (#48414) | [upstream](https://github.com/sgl-project/sglang/pull/48414) | L1 |",
            "- Priorities follow the roadmap ([#46462](https://github.com/sgl-project/sglang/issues/46462)).",
            "  | [#50842](https://github.com/sgl-project/sglang/pull/50842) | 2026-11-18 | CUDA-first feature |",
            "    - [#30599](https://github.com/sgl-project/sglang/issues/30599): tracking issue (hand over)",
            "+ [#40624](https://github.com/sgl-project/sglang/issues/40624): feature plan",
            "1. [#44304](https://github.com/sgl-project/sglang/issues/44304)",
            "| #52054 | 2026-11-21 | CUDA-first feature |",
            "| [#50620](https://github.com/sgl-project/sglang/pull/50620) :rocket: Fuse the draft softmax and sample | 2026-11-19 |",
            "2) #50124 [Perf] decode: Copy the top logprobs to the host once per step",
        ])
        self.assertEqual([l.split(":")[0] for l in daily_report.untitled(body)], ["1", "3", "4", "8", "9", "10", "11", "12"])

    def test_check_exit_codes(self):
        template = Path(__file__).resolve().parent.parent / "templates" / "daily_report.md"
        with tempfile.TemporaryDirectory() as d:
            leaky, untitled, long = Path(d) / "leaky.md", Path(d) / "untitled.md", Path(d) / "long.md"
            leaky.write_text(daily_report.MARKER + "\nweights in /data/models/x\n")
            untitled.write_text(daily_report.MARKER + "\n| [#48414](https://github.com/sgl-project/sglang/pull/48414) | 2026-11-16 |\n")
            long.write_text(daily_report.MARKER + "\n" + "x" * (daily_report.ISSUE_LIMIT + 1))
            with redirect_stdout(io.StringIO()) as out, redirect_stderr(io.StringIO()):
                self.assertEqual(daily_report.main(["check", str(template)]), 0, "the template itself is public-safe and titled")
                self.assertEqual(daily_report.main(["check", str(leaky)]), 3)
                self.assertEqual(daily_report.main(["check", str(untitled)]), 5)
                self.assertEqual(daily_report.main(["check", str(long)]), 4)
            self.assertIn('"marker": true', out.getvalue())

    def test_latest_finds_the_newest_shared_report(self):
        items = [self.issue(9, "2026-11-07", marker=False), {"number": 4, "title": "a PR", "pull_request": {}, "body": daily_report.MARKER},
                 self.issue(8, "2026-11-06"), self.issue(6, "2026-11-05")]
        with mock.patch.object(daily_report, "get", return_value=items):
            got = daily_report.latest("At1a8/inferpilot")
        self.assertEqual((got["date"], got["number"], got["created_at"]), ("2026-11-06", 8, "2026-11-06T01:00:00Z"))
        self.assertIsInstance(got["age_hours"], float)
        now = daily_report.dt.datetime(2026, 11, 7, 0, 30, tzinfo=daily_report.dt.timezone.utc)
        self.assertEqual(daily_report.age_hours("2026-11-06T01:00:00Z", now), 23.5)
        with mock.patch.object(daily_report, "get", return_value=[]):
            self.assertEqual(daily_report.latest("At1a8/inferpilot"), {})

    def test_latest_reads_with_github_token_alone(self):
        seen = []

        def urlopen(req, timeout=0):
            seen.append(req.get_header("Authorization"))
            return io.BytesIO(b"[]")

        with mock.patch.object(daily_report.urllib.request, "urlopen", urlopen):
            for env in ({"GITHUB_TOKEN": "g", "GH_TOKEN": "h"}, {"GH_TOKEN": "h"}):
                with mock.patch.dict(os.environ, env, clear=True):
                    daily_report.latest("At1a8/inferpilot")
        self.assertEqual(seen, ["Bearer g", None])

    def test_a_root_this_machine_mounts_storage_on_is_local_whatever_its_name(self):
        with tempfile.TemporaryDirectory() as d:
            mounts = Path(d) / "mounts"
            mounts.write_text("overlay / overlay rw 0 0\nproc /proc proc rw 0 0\n/dev/sdb1 /lustre ext4 rw 0 0\n"
                              "/dev/sdb1 /etc/hosts ext4 rw 0 0\n/dev/sdc1 /var xfs rw 0 0\nnfs:/v /gpfs/x nfs rw 0 0\n"
                              "/dev/sdd1 /fast\\040disk ext4 rw 0 0\n")
            self.assertEqual(daily_report.mounted_roots(str(mounts)), ("fast disk", "gpfs", "lustre"),
                             "the kernel's \\040 read as the space it stands for; not the system's own: /etc, /proc, /var")
            self.assertEqual(daily_report.mounted_roots(str(Path(d) / "none")), ())
        local = daily_report.local_paths(("lustre", "fast disk"))
        for line in ("weights in /lustre/models/x", "weights in /" + "srv/models/x", "weights in /fast disk/x", "model_path=/lustre",
                     "model_path=/" + "data"):
            self.assertTrue(local.search(line), line)
        for line in ("logs in /var/log/x.log", "see /usr/lib/python3/x.py", "see /database/x", "huggingface.co/models", "a/b/data"):
            self.assertFalse(local.search(line), line)

    def test_a_secret_stops_the_report_and_is_listed_redacted(self):
        token = "ghp_" + "A1b2" * 9
        found = daily_report.leaks("ok\nCI said: export GITHUB_TOKEN=%s for the job\n" % token)
        self.assertEqual(len(found), 1)
        self.assertTrue(found[0].startswith("2: ") and "<secret>" in found[0] and token not in found[0], found[0])
        self.assertEqual(daily_report.verdict(daily_report.check("a key %s here\n" % token)), 3)

    def test_latest_pages_until_a_report_and_takes_the_latest_date(self):
        prs = [{"number": 100 + k, "title": "a PR", "pull_request": {}, "body": daily_report.MARKER} for k in range(100)]
        late = self.issue(8, "2026-11-22", created="T09:00:00Z")
        newest = self.issue(7, "2026-11-23", created="T01:00:00Z")
        with mock.patch.object(daily_report, "get", side_effect=[prs, [late, newest]]) as get:
            got = daily_report.latest("At1a8/inferpilot")
        self.assertEqual(get.call_count, 2, "a page of pull requests alone holds no report: the next page is read")
        self.assertEqual((got["date"], got["number"]), ("2026-11-23", 7), "the latest date, though the older report was shared later")

    def test_it_never_writes(self):
        src = (Path(__file__).resolve().parent.parent / "scripts" / "daily_report.py").read_text()
        self.assertNotRegex(src, r"method=|\"POST\"|\"PATCH\"|\"PUT\"|\"DELETE\"")


class ShareDailyReportTest(unittest.TestCase):
    """The daily report as an issue in InferPilot's repository, with GITHUB_TOKEN; GitHub is mocked."""

    REPORT = (daily_report.MARKER + "\n# InferPilot daily report 2026-11-23 — sglang main `2be81e0ddc`\n\n"
              "| [#48414](https://github.com/sgl-project/sglang/pull/48414) [Feat] kimi_k3: Merge the Day-0 branch | 2026-11-16 |\n")

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.report = Path(tmp.name) / "daily_report.md"

    def share(self, body=None, token="t", shared=None, post=None, flags=(), after=None):
        """after: what the repository's issues show when they are read again after a post GitHub did not answer."""
        self.report.write_text(self.REPORT if body is None else body)
        env = {k: v for k, v in os.environ.items() if k != "GITHUB_TOKEN"}
        if token:
            env["GITHUB_TOKEN"] = token
        posted = []

        def fake_post(repo, title, text, tok):
            posted.append((repo, title, text, tok))
            if isinstance(post, Exception):
                raise post
            return {"number": 7, "html_url": "https://github.com/At1a8/inferpilot/issues/7"}

        self.lookups = mock.MagicMock(side_effect=[shared or {}] + ([] if after is None else [after]))
        with mock.patch.dict(os.environ, env, clear=True), redirect_stdout(io.StringIO()) as out, \
                redirect_stderr(io.StringIO()) as err, mock.patch.object(daily_report, "latest", self.lookups), \
                mock.patch.object(share_daily_report, "post", side_effect=fake_post):
            rc = share_daily_report.main([str(self.report), "--repo", "At1a8/inferpilot", *flags])
        return rc, out.getvalue(), err.getvalue(), posted

    def test_what_is_checked_is_what_is_posted(self):
        checked = []
        check = daily_report.check
        with mock.patch.object(daily_report, "check", side_effect=lambda body, host="": checked.append(body) or check(body, host)):
            rc, out, err, posted = self.share()
        self.assertEqual((rc, checked), (0, [posted[0][2]]), "the body read once is the one checked and the one posted")
        self.assertEqual(posted[0][3], "t", "posted with GITHUB_TOKEN")

    def test_a_post_github_did_not_answer_is_looked_up_before_anything_else(self):
        timeout = share_daily_report.urllib.error.URLError("timed out")
        day = {"date": "2026-11-23", "number": 9, "url": "https://github.com/At1a8/inferpilot/issues/9"}
        rc, out, err, posted = self.share(post=timeout, after=day)
        self.assertEqual((rc, json.loads(out)["action"], json.loads(out)["url"]), (0, "created", day["url"]), "opened all the same")
        rc, out, err, posted = self.share(post=timeout, after={})
        self.assertEqual((rc, out), (1, ""))
        self.assertIn("share it by hand", err)
        rc, out, err, posted = self.share(post=timeout, after=share_daily_report.urllib.error.URLError("down"))
        self.assertEqual((rc, out), (6, ""), "cannot tell")
        self.assertIn("before sharing it by hand", err)
        truncated = share_daily_report.http.client.IncompleteRead(b"{")
        self.assertEqual(self.share(post=truncated, after=day)[0], 0, "a truncated answer is no answer either")
        self.assertEqual(self.share(post=timeout, after=truncated)[0], 6, "nor one read back truncated")
        self.assertEqual(self.share(shared=truncated)[0::3], (1, []), "the first lookup cut short posts nothing")
        bad_gateway = share_daily_report.urllib.error.HTTPError("https://api.github.com", 502, "Bad Gateway", {}, None)
        self.assertEqual(self.share(post=bad_gateway, after=day)[0], 0, "a 5xx is no answer either")
        invalid = share_daily_report.urllib.error.HTTPError("https://api.github.com", 422, "Unprocessable", {}, None)
        self.assertEqual((self.share(post=invalid)[0], self.lookups.call_count), (1, 1), "a 4xx is GitHub's answer: no second look")

    def test_a_checked_report_becomes_the_days_issue(self):
        rc, out, err, posted = self.share()
        self.assertEqual(rc, 0, err)
        self.assertEqual(json.loads(out)["url"], "https://github.com/At1a8/inferpilot/issues/7")
        self.assertEqual(posted, [("At1a8/inferpilot", "InferPilot daily report 2026-11-23", self.REPORT, "t")],
                         "titled as daily_report.py latest reads it back, the whole file as the body")

    def test_that_days_report_shared_already_is_not_posted_again_with_or_without_a_token(self):
        day = {"date": "2026-11-23", "number": 5, "url": "https://github.com/At1a8/inferpilot/issues/5"}
        for token in ("t", ""):
            rc, out, err, posted = self.share(token=token, shared=day)
            self.assertEqual((rc, json.loads(out)["action"], json.loads(out)["url"], posted), (0, "exists", day["url"], []), err)
        rc, out, err, posted = self.share(shared={**day, "date": "2026-11-22"})
        self.assertEqual((rc, len(posted)), (0, 1), "the day before's report does not stop today's")

    def test_a_report_older_than_the_latest_shared_is_not_posted(self):
        newer = {"date": "2026-11-24", "number": 9, "url": "https://github.com/At1a8/inferpilot/issues/9"}
        rc, out, err, posted = self.share(shared=newer)
        self.assertEqual((rc, out, posted), (1, "", []))
        self.assertIn("newer than this one of 2026-11-23", err)

    def test_nothing_is_posted_without_a_token_or_a_clean_report(self):
        self.assertEqual(self.share(token="")[0::3], (2, []))
        self.assertEqual(self.share(body=self.REPORT + "weights in /data/models/x\n")[0::3], (3, []))
        self.assertEqual(self.share(body=self.REPORT + "| [#46784](https://github.com/sgl-project/sglang/pull/46784) | x |\n")[0::3], (5, []))
        self.assertEqual(self.share(body=self.REPORT.replace(daily_report.MARKER, ""))[0::3], (1, []), "no marker")
        self.assertEqual(self.share(body=self.REPORT.replace("# InferPilot daily report 2026-11-23", "# Report"))[0::3], (1, []))
        rc, out, err, posted = self.share(flags=["--dry-run"])
        self.assertEqual((rc, json.loads(out)["title"], posted), (0, "InferPilot daily report 2026-11-23", []))

    def test_a_token_that_cannot_open_issues_is_named(self):
        denied = share_daily_report.urllib.error.HTTPError("https://api.github.com", 403, "Forbidden", {}, None)
        rc, out, err, posted = self.share(post=denied)
        self.assertEqual((rc, out, self.lookups.call_count), (2, "", 1))
        self.assertIn("GITHUB_TOKEN cannot open issues", err)
        self.assertIn("share it by hand", err)


@unittest.skipUnless(shutil.which("bash"), "needs bash")
class EvalAccuracyTest(unittest.TestCase):
    """scripts/eval_accuracy.sh against a fake server (curl) and a fake evalscope."""

    SCRIPT = SCRIPTS / "eval_accuracy.sh"

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        fake = self.tmp / "bin"
        fake.mkdir()
        self.calls = self.tmp / "evalscope_calls.txt"
        # The server answers /models and /server_info; the judge probe (the call with -w) answers FAKE_JUDGE.
        (fake / "curl").write_text(textwrap.dedent("""\
            #!/bin/bash
            url="${@: -1}"
            case " $* " in *" -w "*) echo -n "${FAKE_JUDGE:-401}"; exit 0 ;; esac
            case "$url" in
              */v1/models) if [ -n "${FAKE_CONTEXT:-}" ]; then
                             echo '{"data": [{"id": "fake-model", "max_model_len": '"$FAKE_CONTEXT"'}]}'
                           else
                             echo '{"data": [{"id": "fake-model"}]}'
                           fi ;;
              */server_info) echo '{"internal_states": [{"avg_spec_accept_length": 4.0}]}' ;;
            esac
            """))
        (fake / "evalscope").write_text(textwrap.dedent("""\
            #!/bin/bash
            echo "$*" >> "$CALLS"
            printf '%s\\n' "$@" > "$CALLS.argv"
            [ -n "${FAKE_NO_REPORT:-}" ] && exit 1
            jm=""
            while [ $# -gt 0 ]; do
              case "$1" in --work-dir) wd=$2; shift ;; --datasets) ds=$2; shift ;; --judge-model-args) jm=$2; shift ;; esac
              shift
            done
            echo "work dir $wd"
            # as evalscope does: its arguments, the judge's among them, in its configs and its log
            mkdir -p "$wd/configs" "$wd/run/reports/fake-model"
            echo "judge_model_args: $jm" > "$wd/configs/task_config.yaml"
            echo "judge_model_args=$jm"
            [ -n "${FAKE_READONLY:-}" ] && chmod 444 "$wd/configs/task_config.yaml"
            # a directory of its files that cannot be listed
            if [ -n "${FAKE_LOCKED_DIR:-}" ]; then
              mkdir -p "$wd/configs/locked"
              echo "judge_model_args: $jm" > "$wd/configs/locked/cfg.yaml"
              chmod 000 "$wd/configs/locked"
            fi
            # a directory of its files that can be listed, and its files not looked at
            if [ -n "${FAKE_NO_SEARCH_DIR:-}" ]; then
              mkdir -p "$wd/configs/listed"
              echo "judge_model_args: $jm" > "$wd/configs/listed/cfg.yaml"
              chmod 444 "$wd/configs/listed"
            fi
            # a child that outlives it, holds its output open and writes its arguments later on
            if [ -n "${FAKE_CHILD:-}" ]; then
              ( sleep "$FAKE_CHILD"; echo "judge_model_args: $jm" > "$wd/configs/child.yaml" ) &
              echo $! > "$CALLS.child"
            fi
            # the script stopped while evalscope runs (a timeout, the agent's call ending)
            [ -n "${FAKE_STOPPED:-}" ] && { kill -TERM "$PPID"; exit 143; }
            # a run that takes a while, and writes its arguments again later on
            if [ -n "${FAKE_SLOW:-}" ]; then
              echo $$ > "$CALLS.pid"
              [ -n "${FAKE_IGNORE_TERM:-}" ] && trap '' TERM
              # more than a pipe holds
              if [ -n "${FAKE_FLOOD:-}" ]; then
                line=$(printf 'x%.0s' $(seq 1024))
                for _ in $(seq 300); do echo "$line"; sleep 0.01; done
              fi
              sleep "$FAKE_SLOW"
              echo "judge_model_args: $jm" > "$wd/configs/later.yaml"
            fi
            # every question answered, unless FAKE_NUM says how many were
            case "$ds" in gpqa_diamond) n=198 ;; simple_qa) n=4326 ;; mmlu_pro) n=12032 ;; hle) n=2158 ;; *) n=1319 ;; esac
            echo '{"metrics": [{"name": "mean_acc", "score": 0.97, "num": '"${FAKE_NUM:-$n}"'}]}' > "$wd/run/reports/fake-model/$ds.json"
            """))
        for f in fake.iterdir():
            f.chmod(0o755)
        self.env = {"PATH": "%s:/usr/bin:/bin" % fake, "HOME": str(self.tmp), "CALLS": str(self.calls),
                    "API_URL": "http://fake:30010/v1", "RESULT_ROOT": str(self.tmp / "results")}

    def run_script(self, *args, **env):
        return subprocess.run(["bash", str(self.SCRIPT), *args], env={**self.env, **env},
                              capture_output=True, text=True, timeout=60)

    def test_parses_and_holds_no_secret(self):
        self.assertEqual(subprocess.run(["bash", "-n", str(self.SCRIPT)]).returncode, 0)
        text = self.SCRIPT.read_text()
        self.assertIsNone(re.search(r"\b[0-9a-f]{32}\b", text), "a hex key in the script")
        self.assertIsNone(re.search(r"hf_[A-Za-z0-9]{20,}", text), "a Hugging Face token in the script")
        self.assertIsNone(re.search(r"(KEY|TOKEN|NTID):-[^}$]", text), "a key, token or user name as a default")

    def test_gsm8k_once_by_default(self):
        r = self.run_script()
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        calls = [line for line in self.calls.read_text().splitlines() if line.startswith("eval ")]
        self.assertEqual(len(calls), 1)
        self.assertIn("--datasets gsm8k", calls[0])
        self.assertIn("--repeats 1", calls[0])
        summary = (self.tmp / "results" / "summary.txt").read_text()
        self.assertIn("eval gsm8k: accept_length=[4.0]", summary)
        self.assertIn("'num': 1319", summary)

    def test_thinking_is_switched_with_both_template_keys(self):
        def gen(**env):
            self.assertEqual(self.run_script("gsm8k", **env).returncode, 0)
            argv = Path(str(self.calls) + ".argv").read_text().splitlines()
            return json.loads(argv[argv.index("--generation-config") + 1])["extra_body"]
        on = gen()
        self.assertEqual((on["chat_template_kwargs"], on["reasoning_effort"]), ({"thinking": True, "enable_thinking": True}, "max"))
        off = gen(ENABLE_THINKING="0", REASONING_EFFORT="")
        self.assertEqual(off, {"chat_template_kwargs": {"thinking": False, "enable_thinking": False}},
                         "Qwen3 thinks unless enable_thinking is false")

    def test_every_run_samples_afresh_and_reads_only_its_own_report(self):
        r = self.run_script("gsm8k", ENABLE_THINKING="0", REASONING_EFFORT="", CACHE_DIR=str(self.tmp / "old"),
                            RERUN_REVIEW="1")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        argv = Path(str(self.calls) + ".argv").read_text().splitlines()
        self.assertNotIn("--use-cache", argv, "no earlier run's answers, whatever the environment says")
        self.assertNotIn("--rerun-review", argv)
        self.assertIn("--no-timestamp", argv)
        run_dir = Path(argv[argv.index("--work-dir") + 1])
        self.assertEqual(run_dir.parent, self.tmp / "results" / "eval_gsm8k")
        self.assertIn('chat_template_kwargs={"thinking":false,"enable_thinking":false}',
                      (run_dir / "sampling_settings.txt").read_text())
        again = self.run_script("gsm8k", FAKE_NO_REPORT="1")
        self.assertEqual(again.returncode, 1, "a run without a report of its own took the earlier run's")
        self.assertIn("no report", again.stderr)

    def test_each_run_keeps_its_own_log_and_the_summary_names_its_directory(self):
        for _ in range(2):
            self.assertEqual(self.run_script("gsm8k").returncode, 0)
        runs = sorted((self.tmp / "results" / "eval_gsm8k").iterdir())
        self.assertEqual(len(runs), 2)
        for run in runs:
            self.assertIn(str(run), (run / "evalscope.log").read_text(), "another run overwrote this run's log")
        lines = (self.tmp / "results" / "summary.txt").read_text().splitlines()
        self.assertEqual(sorted(line.rsplit(" dir=", 1)[1] for line in lines), ["eval_gsm8k/" + run.name for run in runs])

    def test_options_reach_evalscope_whole(self):
        data = self.tmp / "long bench"
        data.mkdir()
        (data / "default_test.jsonl").write_text('{"q": 1}\n')
        r = self.run_script("longbench_v2", LONGBENCH_LOCAL_PATH=str(data), LIMIT="5")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        argv = Path(str(self.calls) + ".argv").read_text().splitlines()
        self.assertEqual(json.loads(argv[argv.index("--dataset-args") + 1])["longbench_v2"]["local_path"], str(data))
        self.assertEqual(argv[argv.index("--limit") + 1], "5")

    def test_the_judge_key_reaches_only_a_judged_set_and_no_result_keeps_it(self):
        key = "judge-key-for-the-test-7f3a"

        def holding_key():
            return sorted(str(p.relative_to(self.tmp)) for p in (self.tmp / "results").rglob("*")
                          if p.is_file() and key in p.read_text(errors="replace"))
        r = self.run_script("gsm8k", JUDGE_KEY=key, FAKE_JUDGE="200")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        argv = Path(str(self.calls) + ".argv").read_text().splitlines()
        self.assertNotIn("--judge-model-args", argv, "gsm8k is graded by rule: no judge, no key")
        self.assertEqual(holding_key(), [])
        r = self.run_script("simple_qa", LLM_GATEWAY_KEY=key, FAKE_JUDGE="200")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        argv = Path(str(self.calls) + ".argv").read_text().splitlines()
        self.assertEqual(argv[argv.index("--judge-strategy") + 1], "auto")
        self.assertIn(key, argv[argv.index("--judge-model-args") + 1], "simple_qa's judge needs the key")
        self.assertEqual(holding_key(), [], "evalscope's configs and log keep the key")
        run = next((self.tmp / "results" / "eval_simple_qa").iterdir())
        self.assertIn("<JUDGE_KEY>", (run / "configs" / "task_config.yaml").read_text())
        self.assertIn("<JUDGE_KEY>", (run / "evalscope.log").read_text())
        r = self.run_script("simple_qa", LLM_GATEWAY_KEY=key, FAKE_JUDGE="200", FAKE_STOPPED="1")
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(holding_key(), [], "a run stopped on its way keeps the key")

    KEY = "judge-key-for-the-test-7f3a"

    def holding_key(self):
        return sorted(str(p.relative_to(self.tmp)) for p in (self.tmp / "results").rglob("*")
                      if p.is_file() and self.KEY in p.read_text(errors="replace"))

    def start_slow_run(self, seconds, **env):
        """simple_qa with a judge, its evalscope running for `seconds`; returns the script and evalscope's pid."""
        p = subprocess.Popen(["bash", str(self.SCRIPT), "simple_qa"], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             text=True, env={**self.env, "LLM_GATEWAY_KEY": self.KEY, "FAKE_JUDGE": "200",
                                             "FAKE_SLOW": str(seconds), **env})
        self.addCleanup(lambda: p.poll() is None and p.kill())
        pidfile = Path(str(self.calls) + ".pid")
        deadline = time.time() + 30
        while not (pidfile.exists() and pidfile.read_text().strip()) and time.time() < deadline:
            time.sleep(0.05)
        pid = int(pidfile.read_text())

        def gone():
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        self.addCleanup(gone)
        return p, pid

    def test_the_log_shows_no_key_while_evalscope_runs(self):
        p, _ = self.start_slow_run(3)
        log = None
        deadline = time.time() + 10
        while time.time() < deadline:
            logs = list((self.tmp / "results").rglob("evalscope.log"))
            if logs and "judge_model_args=" in logs[0].read_text(errors="replace"):
                log = logs[0].read_text(errors="replace")
                break
            time.sleep(0.05)
        self.assertIsNone(p.poll(), "evalscope ended before its log was read")
        self.assertIsNotNone(log, "evalscope's line never reached its log")
        self.assertNotIn(self.KEY, log, "an agent reading the log while the run goes sees the key")
        self.assertIn("judge_model_args=", log)
        out, err = p.communicate(timeout=60)
        self.assertEqual(p.returncode, 0, err + out)
        self.assertEqual(self.holding_key(), [])

    def test_a_stopped_run_stops_evalscope_before_it_redacts(self):
        p, pid = self.start_slow_run(3)
        p.send_signal(signal.SIGTERM)  # to the script alone, as a timeout of the agent's shell does
        p.communicate(timeout=60)
        self.assertNotEqual(p.returncode, 0)
        with self.assertRaises(ProcessLookupError, msg="evalscope runs on after the script and writes the key again"):
            os.kill(pid, 0)
        self.assertEqual(self.holding_key(), [])

    @unittest.skipUnless(Path("/proc/self/status").exists(), "reads the ignored signals in /proc")
    def test_the_redaction_ignores_the_signals_of_the_runners_shutdown(self):
        wrap = self.tmp / "wrap"
        wrap.mkdir()
        (wrap / "python3").write_text(textwrap.dedent("""\
            #!/bin/bash
            [ "$1" = "-" ] && grep SigIgn /proc/self/status >> "$CALLS.sigign"
            exec %s "$@"
            """ % shlex.quote(sys.executable)))
        (wrap / "python3").chmod(0o755)
        r = self.run_script("simple_qa", LLM_GATEWAY_KEY=self.KEY, FAKE_JUDGE="200",
                            PATH="%s:%s" % (wrap, self.env["PATH"]))
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        masks = [int(line.split()[1], 16) for line in Path(str(self.calls) + ".sigign").read_text().splitlines()]
        self.assertTrue(masks, "the redaction did not run")
        for mask in masks:
            for sig in (signal.SIGHUP, signal.SIGINT, signal.SIGTERM):
                self.assertTrue(mask >> (sig - 1) & 1, "%s stops the redaction halfway" % sig.name)
        self.assertEqual(self.holding_key(), [])

    @unittest.skipIf(os.geteuid() == 0, "root writes a read-only file")
    def test_a_file_the_redaction_cannot_rewrite_stops_the_script(self):
        r = self.run_script("simple_qa", LLM_GATEWAY_KEY=self.KEY, FAKE_JUDGE="200", FAKE_READONLY="1")
        self.assertEqual(r.returncode, 1, r.stderr + r.stdout)
        self.assertIn("task_config.yaml", r.stderr, "no word of the file that keeps the key")
        self.assertIn("could not be removed", r.stderr)
        summary = self.tmp / "results" / "summary.txt"
        self.assertFalse(summary.exists() and "simple_qa" in summary.read_text(),
                         "the results of a run that still holds the key were read")

    @unittest.skipIf(os.geteuid() == 0, "root lists any directory")
    def test_a_directory_the_redaction_cannot_list_stops_the_script(self):
        r = self.run_script("simple_qa", LLM_GATEWAY_KEY=self.KEY, FAKE_JUDGE="200", FAKE_LOCKED_DIR="1")
        for locked in (self.tmp / "results").rglob("locked"):
            locked.chmod(0o755)
        self.assertEqual(r.returncode, 1, r.stderr + r.stdout)
        self.assertIn("configs/locked", r.stderr, "no word of the directory whose files may keep the key")
        self.assertIn("could not be removed", r.stderr)
        summary = self.tmp / "results" / "summary.txt"
        self.assertFalse(summary.exists() and "simple_qa" in summary.read_text(),
                         "the results of a run that may still hold the key were read")

    @unittest.skipIf(os.geteuid() == 0, "root looks at any file")
    def test_a_file_the_redaction_cannot_look_at_stops_the_script(self):
        r = self.run_script("simple_qa", LLM_GATEWAY_KEY=self.KEY, FAKE_JUDGE="200", FAKE_NO_SEARCH_DIR="1")
        for listed in (self.tmp / "results").rglob("listed"):
            listed.chmod(0o755)
        self.assertEqual(r.returncode, 1, r.stderr + r.stdout)
        self.assertIn("configs/listed/cfg.yaml", r.stderr, "no word of the file that may keep the key")
        self.assertIn("could not be removed", r.stderr)
        self.assertEqual(self.holding_key(), ["results/eval_simple_qa/%s/configs/listed/cfg.yaml" % run.name
                                              for run in (self.tmp / "results" / "eval_simple_qa").iterdir()],
                         "the file the redaction could not look at is the one that keeps the key")
        summary = self.tmp / "results" / "summary.txt"
        self.assertFalse(summary.exists() and "simple_qa" in summary.read_text(),
                         "the results of a run that may still hold the key were read")

    def test_without_setsid_nothing_starts(self):
        r = subprocess.run([shutil.which("bash"), str(self.SCRIPT), "gsm8k"], env={**self.env, "PATH": str(self.tmp / "bin")},
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 2, r.stderr + r.stdout)
        self.assertIn("setsid", r.stderr)
        self.assertFalse(self.calls.exists(), "evalscope ran in the script's own process group")

    @staticmethod
    def running(pid):
        try:
            return Path("/proc/%d/stat" % pid).read_text().rsplit(")", 1)[1].split()[0] != "Z"
        except OSError:
            return False

    def test_an_evalscope_that_ignores_sigterm_is_stopped_in_time_for_the_redaction(self):
        p, pid = self.start_slow_run(60, FAKE_IGNORE_TERM="1")
        began = time.time()
        p.send_signal(signal.SIGTERM)
        p.communicate(timeout=60)
        took = time.time() - began
        self.assertEqual(p.returncode, 143)
        self.assertLess(took, 9, "the runner SIGKILLs what is left of a run 10 seconds after its SIGTERM: the key would stay")
        self.assertFalse(self.running(pid), "evalscope runs on after the script")
        self.assertEqual(self.holding_key(), [])

    def test_a_stop_before_evalscope_leads_its_group_stops_the_launch(self):
        wrap = self.tmp / "wrap"
        wrap.mkdir()
        # a setsid that takes its time: one process, which leads no group yet and has started nothing
        (wrap / "setsid").write_text(textwrap.dedent("""\
            #!/bin/bash
            echo $$ > "$CALLS.setsid"
            exec %s -c 'import os, sys, time; time.sleep(15); os.execv(sys.argv[1], ["setsid"] + sys.argv[2:])' %s "$@"
            """ % (shlex.quote(sys.executable), shlex.quote(shutil.which("setsid") or "/usr/bin/setsid"))))
        (wrap / "setsid").chmod(0o755)
        p = subprocess.Popen(["bash", str(self.SCRIPT), "simple_qa"], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             text=True, env={**self.env, "LLM_GATEWAY_KEY": self.KEY, "FAKE_JUDGE": "200",
                                             "PATH": "%s:%s" % (wrap, self.env["PATH"])})
        self.addCleanup(lambda: p.poll() is None and p.kill())
        started = Path(str(self.calls) + ".setsid")
        deadline = time.time() + 30
        while not (started.exists() and started.read_text().strip()) and time.time() < deadline:
            time.sleep(0.05)
        launch = int(started.read_text())
        self.addCleanup(lambda: self.running(launch) and os.kill(launch, signal.SIGKILL))
        began = time.time()
        p.send_signal(signal.SIGTERM)
        p.communicate(timeout=60)
        took = time.time() - began
        self.assertEqual(p.returncode, 143)
        self.assertLess(took, 8, "the script waited for a launch it had not stopped: the runner SIGKILLs it first, and the key stays")
        self.assertFalse(self.running(launch), "the launch runs on after the script")
        self.assertFalse(self.calls.exists(), "evalscope started after the stop")
        self.assertEqual(self.holding_key(), [])

    def test_an_evalscope_whose_log_nobody_writes_any_more_is_stopped_not_waited_for(self):
        wrap = self.tmp / "wrap"
        wrap.mkdir()
        # the process that masks the key in the log (python3 -u -c ...) fails after the first of what it reads
        (wrap / "python3").write_text(textwrap.dedent("""\
            #!/bin/bash
            [ "$1" = "-u" ] && { head -c 1000 > /dev/null; exit 1; }
            exec %s "$@"
            """ % shlex.quote(sys.executable)))
        (wrap / "python3").chmod(0o755)
        # evalscope prints more than a pipe holds, and has a minute to go
        p, pid = self.start_slow_run(60, FAKE_FLOOD="1", PATH="%s:%s" % (wrap, self.env["PATH"]))
        try:
            out, err = p.communicate(timeout=30)
        except subprocess.TimeoutExpired:
            p.kill()
            p.communicate()
            self.fail("the script waits for an evalscope that waits on a pipe nobody reads")
        self.assertEqual(p.returncode, 1, err + out)
        self.assertIn("ended before it", err)
        self.assertIn("its log is not whole", err)
        self.assertFalse(self.running(pid), "evalscope runs on, with nothing reading what it prints")
        self.assertEqual(self.holding_key(), [])
        summary = self.tmp / "results" / "summary.txt"
        self.assertFalse(summary.exists() and "simple_qa" in summary.read_text(), "a set whose log is not whole was given a score")

    def test_a_child_evalscope_left_is_stopped_before_the_log_ends_and_the_key_is_removed(self):
        began = time.time()
        r = self.run_script("simple_qa", LLM_GATEWAY_KEY=self.KEY, FAKE_JUDGE="200", FAKE_CHILD="20")
        took = time.time() - began
        child = int(Path(str(self.calls) + ".child").read_text())
        self.addCleanup(lambda: self.running(child) and os.kill(child, signal.SIGKILL))
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertLess(took, 15, "the script waited for the child to close the log's pipe")
        self.assertFalse(self.running(child), "a child of evalscope's outlived it, and can write the key after the redaction")
        self.assertEqual(self.holding_key(), [])
        self.assertIn("eval simple_qa:", (self.tmp / "results" / "summary.txt").read_text())

    def test_the_log_ends_though_no_writer_opened_its_pipe_and_a_writer_that_stays_does_not_hold_it(self):
        found = [re.search(r"^%s\(\) \{.*?^\}\n" % name, self.SCRIPT.read_text(), re.S | re.M) for name in ("start_log", "end_log")]
        self.assertNotIn(None, found, "eval_accuracy.sh has no start_log or end_log")
        # the script's two functions, with cat for the masker; no writer at all, one that ends, one that stays
        body = "".join(f.group(0) for f in found) + textwrap.dedent("""\
            mask_key() { exec cat; }
            FIFO_DIR=$1
            mkfifo "$FIFO_DIR/out"
            start_log "$FIFO_DIR/log"
            case "$2" in
                written) echo "a line" > "$FIFO_DIR/out" 3>&- ;;
                held) sleep 30 > "$FIFO_DIR/out" 2> /dev/null 3>&- &
                      echo $! > "$FIFO_DIR/writer" ;;
            esac
            end_log
            echo "status $?"
            """)
        for case, whole in (("none", True), ("written", True), ("held", False)):
            d = tempfile.mkdtemp(dir=self.tmp)
            began = time.time()
            r = subprocess.run(["bash", "-c", body, "_", d, case], capture_output=True, text=True, timeout=30)
            took = time.time() - began
            writer = Path(d) / "writer"
            if writer.exists():
                os.kill(int(writer.read_text()), signal.SIGKILL)
            self.assertLess(took, 8, "%s: the script hangs on a log that never ends" % case)
            self.assertEqual(r.stdout.split()[-1] == "0", whole, "%s: %s" % (case, r.stdout + r.stderr))
            self.assertEqual((Path(d) / "log").read_text(), "a line\n" if case == "written" else "", case)

    def test_a_set_whose_log_is_not_whole_has_no_score(self):
        wrap = self.tmp / "wrap"
        wrap.mkdir()
        # the process that masks the key in the log (python3 -u -c ...) reads what it gets and fails
        (wrap / "python3").write_text(textwrap.dedent("""\
            #!/bin/bash
            [ "$1" = "-u" ] && { cat > /dev/null; exit 1; }
            exec %s "$@"
            """ % shlex.quote(sys.executable)))
        (wrap / "python3").chmod(0o755)
        r = self.run_script("gsm8k", PATH="%s:%s" % (wrap, self.env["PATH"]))
        self.assertEqual(r.returncode, 1, r.stderr + r.stdout)
        self.assertIn("its log is not whole", r.stderr)
        summary = self.tmp / "results" / "summary.txt"
        self.assertFalse(summary.exists() and "gsm8k" in summary.read_text(), "a set whose log is not whole was given a score")

    def test_max_tokens_keeps_room_for_the_question_in_the_servers_context(self):
        def sent():
            argv = Path(str(self.calls) + ".argv").read_text().splitlines()
            return json.loads(argv[argv.index("--generation-config") + 1])["max_tokens"]
        r = self.run_script("gsm8k", FAKE_CONTEXT="40960")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertEqual(sent(), 32768, "the default fits a 40960-token context (Qwen3) as it is")
        self.assertNotIn("lowered", r.stdout)
        for context, given, want in (("32768", None, 32768 - 2048), ("40960", "102400", 40960 - 2048)):
            r = self.run_script("gsm8k", FAKE_CONTEXT=context, **({"MAX_TOKENS": given} if given else {}))
            self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
            self.assertEqual(sent(), want, (context, given))
            self.assertIn("lowered to %d" % want, r.stdout)
        r = self.run_script("gsm8k", FAKE_CONTEXT="2048")
        self.assertEqual(r.returncode, 1)
        self.assertIn("leaves no room", r.stderr)

    def test_a_set_with_an_unanswered_question_has_no_score(self):
        r = self.run_script("gsm8k", FAKE_NUM="1300")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("not every question got an answer", r.stderr)
        summary = self.tmp / "results" / "summary.txt"
        self.assertFalse(summary.exists() and "gsm8k:" in summary.read_text(), "a score over 1300 of 1319 questions was kept")
        r = self.run_script("gsm8k", FAKE_NUM="5", LIMIT="5")
        self.assertEqual(r.returncode, 0, "a smoke test asks for fewer: " + r.stderr)
        self.assertIn("eval gsm8k:", summary.read_text())

    def test_a_judged_set_without_a_key_is_skipped(self):
        r = self.run_script("simple_qa")
        self.assertEqual(r.returncode, 1)
        self.assertIn("needs an LLM judge", r.stderr)
        self.assertFalse(self.calls.exists(), "evalscope ran without a judge")

    def test_the_key_comes_from_the_environment_and_stays_out_of_the_results(self):
        r = self.run_script("simple_qa", LLM_GATEWAY_KEY="key-from-env", JUDGE_NTID="someone", FAKE_JUDGE="200")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertIn("key-from-env", self.calls.read_text())
        self.assertNotIn("key-from-env", r.stdout + r.stderr)
        for f in (self.tmp / "results").rglob("*"):
            if f.is_file():
                self.assertNotIn("key-from-env", f.read_text(errors="replace"), f)


class HarnessTest(unittest.TestCase):
    """scripts/harness.py renders templates/harness/ with the task's values, then the feature's, then the templates'."""

    FEATURE = {"CONTEXT_LENGTH": {"value": 4096, "source": "vLLM PR #49406: --max-model-len 4096"},
               "PREFIX_CACHE": {"value": False, "source": "--no-enable-prefix-caching"},
               "OSL": 256, "ENABLE_THINKING": 0}

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.out = self.tmp / "scripts"
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        self.calls = self.tmp / "calls.txt"
        # sglang records its arguments, one per line. python3 passes the import check, records a benchmark run and
        # appends its result with FAKE_COMPLETED of its prompts done (all by default), and runs anything else.
        (self.bin / "sglang").write_text('#!/bin/bash\nprintf "%%s\\n" "$@" > %s\n' % self.calls)
        (self.bin / "python3").write_text(textwrap.dedent("""\
            #!/bin/bash
            [ "$1" = -c ] && exit 0
            if [ "$1" = -m ]; then
              echo "$*" >> %s
              while [ $# -gt 0 ]; do
                case "$1" in --output-file) out=$2; shift ;; --num-prompts) n=$2; shift ;; esac
                shift
              done
              echo "{\\"completed\\": ${FAKE_COMPLETED:-$n}}" >> "$out"
              exit 0
            fi
            exec %s "$@"
            """) % (self.calls, sys.executable))
        for f in self.bin.iterdir():
            f.chmod(0o755)

    def render(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = harness.main(["render", "--out", str(self.out), *args])
        return rc, out.getvalue() + err.getvalue()

    def record(self):
        return json.loads((self.out / "harness.json").read_text())

    def run_rendered(self, name, *args, **env):
        return subprocess.run(["bash", str(self.out / name), *args], capture_output=True, text=True, timeout=60,
                              env={"PATH": "%s:/usr/bin:/bin" % self.bin, "HOME": str(self.tmp), **env})

    def test_every_template_holds_its_parameters_once(self):
        cat = harness.catalog()
        for script in harness.SCRIPTS:
            text = (harness.TEMPLATES / ("%s.sh" % script)).read_text()
            names = [p["name"] for p in harness.parameters(script)]
            self.assertTrue(names, script)
            self.assertEqual([], [n for n in names if n not in text.split("set -u")[0]], "%s.sh's header names its parameters" % script)
        self.assertEqual(len(cat), sum(len(harness.parameters(s)) for s in harness.SCRIPTS))
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(harness.main(["show"]), 0)
        self.assertEqual([], [n for n in cat if "| %s |" % n not in out.getvalue()])

    def test_the_task_wins_over_the_feature_and_the_feature_over_the_template(self):
        rc, msg = self.render("--set", "MODEL_PATH=/models/m", "--set", "CONTEXT_LENGTH=8192",
                              "--workload", '{"isl": 1024, "conc": [1, 8]}',
                              "--feature", json.dumps(self.FEATURE), "--feature-source", "vLLM PR #49406")
        self.assertEqual(rc, 0, msg)
        p = self.record()["parameters"]
        self.assertEqual((p["CONTEXT_LENGTH"]["value"], p["CONTEXT_LENGTH"]["source"]), ("8192", "task"))
        self.assertEqual((p["PREFIX_CACHE"]["value"], p["PREFIX_CACHE"]["source"], p["PREFIX_CACHE"]["why"]),
                         ("0", "feature", "--no-enable-prefix-caching"))
        self.assertEqual((p["OSL"]["value"], p["OSL"]["why"]), ("256", "vLLM PR #49406"))
        self.assertEqual((p["ISL"]["value"], p["CONCURRENCY"]["value"]), ("1024", "1 8"))
        self.assertEqual((p["SEED"]["value"], p["SEED"]["source"]), ("1", "template"))
        self.assertEqual(p["REASONING_EFFORT"]["value"], "", "no reasoning effort without thinking")
        line = next(t for t in (self.out / "server.sh").read_text().splitlines() if t.startswith("PREFIX_CACHE="))
        self.assertTrue(line.startswith('PREFIX_CACHE="0"') and line.endswith("[feature]"), line)
        line = next(t for t in (self.out / "server.sh").read_text().splitlines() if t.startswith("CONTEXT_LENGTH="))
        self.assertTrue(line.startswith('CONTEXT_LENGTH="${CONTEXT_LENGTH-8192}"'), "InferPilot still sets it for a launch: " + line)

    def test_the_same_values_render_the_same_scripts(self):
        """The baseline cache fingerprints a script's contents: no time and no words of the record in it."""
        recipe = self.tmp / "cookbook.txt"
        recipe.write_text(self.COOKBOOK)
        texts = []
        for words in ("vLLM PR #49406", "the PR's README"):
            feature = {"CONTEXT_LENGTH": {"value": 4096, "source": words}, "ENABLE_THINKING": 0}
            rc, msg = self.render("--set", "MODEL_PATH=/models/m", "--feature", json.dumps(feature), "--feature-source", words,
                                  "--cookbook", str(recipe), "--cookbook-source", words)
            self.assertEqual(rc, 0, msg)
            texts.append({n: (self.out / ("%s.sh" % n)).read_text() for n in harness.SCRIPTS})
            self.assertEqual(self.record()["parameters"]["CONTEXT_LENGTH"]["why"], words)
            self.assertEqual(self.record()["cookbook"]["source"], words)
        self.assertEqual(texts[0], texts[1])
        self.assertNotRegex(texts[0]["server"], r"\d{4}-\d\d-\d\dT", "no render time in a script")

    def test_one_script_under_another_name_keeps_the_runs_record(self):
        self.assertEqual(self.render("--scripts", "accuracy")[0], 0)
        before = (self.out / "harness.json").read_text()
        rc, msg = self.render("--scripts", "perf", "--name", "fallback_client", "--feature", json.dumps({"OSL": 256}))
        self.assertEqual(rc, 0, msg)
        self.assertEqual((self.out / "harness.json").read_text(), before, "Setup's record is untouched")
        rec = json.loads((self.out / "fallback_client.json").read_text())
        self.assertEqual(rec["scripts"], {"perf": str(self.out / "fallback_client.sh")})
        self.assertEqual(rec["parameters"]["OSL"]["value"], "256")
        self.assertIn("fallback_client.json beside it", (self.out / "fallback_client.sh").read_text())
        self.assertFalse((self.out / "perf.sh").exists())
        # So the report reads the fallback client's lengths and concurrency from its own record, not from Setup's.
        self.assertNotIn("OSL", json.loads(before)["parameters"], "Setup's record holds a client it did not render")
        report = " ".join((SCRIPTS.parent / "roles" / "director.md").read_text(encoding="utf-8").split())
        self.assertIn("`harness.json` holds only what Setup rendered", report)
        self.assertIn("has its own record beside it, `fallback_client.json`: give the lengths and the concurrency it measured "
                      "with from there", report, "the report would list Setup's values for a client Spec rendered")
        for args in (["--scripts", "perf,accuracy", "--name", "x"], ["--scripts", "perf", "--name", "harness"],
                     ["--scripts", "perf", "--name", "server"], ["--scripts", "perf", "--name", "../x"]):
            rc, msg = self.render(*args)
            self.assertEqual(rc, 2, args)
            self.assertIn("--name", msg)

    def test_a_context_too_small_for_the_answers_is_lifted_for_the_accuracy_server(self):
        rc, msg = self.render("--set", "MODEL_PATH=/models/m", "--feature", json.dumps(self.FEATURE))
        self.assertEqual(rc, 0, msg)
        self.assertEqual(self.record()["accuracy_server_env"], {"CONTEXT_LENGTH": ""})
        self.assertEqual(self.render("--set", "MODEL_PATH=/models/m")[0], 0)
        self.assertEqual(self.record()["accuracy_server_env"], {})
        self.assertEqual(self.render("--set", "MODEL_PATH=/models/m", "--set", "CONTEXT_LENGTH=8192")[0], 0)
        self.assertEqual(self.record()["accuracy_server_env"], {}, "the task's own context stays; MAX_TOKENS is lowered to it")

    def test_mistakes_are_errors(self):
        for args, what in ((["--set", "MAX_MODEL_LEN=4096"], "no template has a parameter MAX_MODEL_LEN"),
                           (["--set", "ISL=8k"], "is not a value"),
                           (["--set", "SERVER_ARGS=--x $(id)"], "is not a value"),
                           (["--workload", '{"isl": 1, "ratio": 2}'], "ratio unknown"),
                           (["--feature", "not json"], "is not a JSON object"),
                           (["--set", "SETS=gsm8k"], "no template has a parameter SETS"),
                           (["--scripts", "server,client"], "--scripts")):
            rc, msg = self.render("--set", "MODEL_PATH=/models/m", *args)
            self.assertEqual(rc, 2, args)
            self.assertIn(what, msg)
        rc, msg = self.render()
        self.assertEqual(rc, 2)
        self.assertIn("MODEL_PATH has no value", msg)
        rc, msg = self.render("--scripts", "perf,accuracy")
        self.assertEqual(rc, 0, "a task with a server of its own needs no MODEL_PATH: " + msg)
        self.assertFalse((self.out / "server.sh").exists())
        for empty in (["--task", ""], ["--task", "  "], ["--feature", ""], ["--workload", ""]):
            self.assertEqual(self.render("--set", "MODEL_PATH=/models/m", *empty)[0], 0, "%s is none" % empty)
        rc, msg = self.render("--scripts", "server,accuracy", "--set", "MODEL_PATH=/models/m",
                              "--workload", '{"dataset": "sharegpt", "conc": 32}')
        self.assertEqual(rc, 0, "the workload of the task's own client is not the template's to read: " + msg)

    def test_the_rendered_server_passes_every_value_and_the_feature_flags_last(self):
        self.assertEqual(self.render("--set", "MODEL_PATH=/models/m", "--feature", json.dumps(self.FEATURE))[0], 0)
        r = self.run_rendered("server.sh", PORT="30001", EXTRA_ARGS="--speculative-algorithm PARD2")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.calls.read_text().split(), ["serve", "--model-path", "/models/m", "--tp", "1", "--host", "127.0.0.1",
                                                          "--port", "30001", "--context-length", "4096", "--disable-radix-cache",
                                                          "--speculative-algorithm", "PARD2"])
        self.assertEqual(self.run_rendered("server.sh", PORT="30001", CONTEXT_LENGTH="").returncode, 0)
        self.assertNotIn("--context-length", self.calls.read_text().split(), "the accuracy server keeps the model's own context")
        self.assertEqual(self.run_rendered("server.sh", PORT="30001", MODEL_PATH="/models/other", PREFIX_CACHE="1").returncode, 0)
        argv = self.calls.read_text().split()
        self.assertTrue(argv[2] == "/models/m" and "--disable-radix-cache" in argv, "an exported value replaces no rendered one")

    def test_a_value_with_an_apostrophe_renders_a_valid_script(self):
        self.assertEqual(self.render("--scripts", "server", "--set", "MODEL_PATH=/models/m",
                                     "--set", "SERVER_ARGS=--chat-template it's")[0], 0)
        r = self.run_rendered("server.sh", PORT="30001")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.calls.read_text().split()[-2:], ["--chat-template", "it's"])

    COOKBOOK = textwrap.dedent("""\
        # FP8 on MI300X/MI325X/MI355X
        SGLANG_USE_AITER=1 HIP_VISIBLE_DEVICES=0,1,2,3 sglang serve \\
          --model-path zai-org/GLM-5.1-FP8 \\
          --tp 8 \\
          --trust-remote-code \\
          --nsa-prefill-backend tilelang \\
          --chunked-prefill-size 131072 \\
          --mem-fraction-static 0.80 \\
          --json-model-override-args '{"index_topk_pattern": "FFSF"}' \\
          --host 0.0.0.0 \\
          --port 30000
        """)

    def test_the_cookbook_recipe_fills_the_server_after_the_task_and_the_feature(self):
        recipe = self.tmp / "cookbook.txt"
        recipe.write_text(self.COOKBOOK)
        rc, msg = self.render("--scripts", "server", "--set", "MODEL_PATH=/models/glm", "--set", "TP=4",
                              "--feature", json.dumps({"CHUNKED_PREFILL_SIZE": {"value": 8192, "source": "the PR"}}),
                              "--cookbook", str(recipe), "--cookbook-source", "GLM-5.1, MI355X FP8")
        self.assertEqual(rc, 0, msg)
        rec = self.record()
        p = rec["parameters"]
        self.assertEqual((p["TP"]["value"], p["TP"]["source"]), ("4", "task"))
        self.assertEqual((p["CHUNKED_PREFILL_SIZE"]["value"], p["CHUNKED_PREFILL_SIZE"]["source"]), ("8192", "feature"))
        self.assertEqual((p["MEM_FRACTION_STATIC"]["value"], p["MEM_FRACTION_STATIC"]["source"], p["MEM_FRACTION_STATIC"]["why"]),
                         ("0.80", "cookbook", "GLM-5.1, MI355X FP8"))
        self.assertEqual((rec["cookbook"]["model"], rec["cookbook"]["env"], rec["cookbook"]["env_dropped"]),
                         ("zai-org/GLM-5.1-FP8", ["SGLANG_USE_AITER=1"], ["HIP_VISIBLE_DEVICES=0,1,2,3"]),
                         "a recipe's setting of the run's GPUs is dropped, and recorded")
        self.assertNotIn("zai-org/GLM-5.1-FP8", (self.out / "server.sh").read_text(), "the recipe's model stays in the record")
        (self.bin / "sglang").write_text('#!/bin/bash\necho "SGLANG_USE_AITER=$SGLANG_USE_AITER HIP_VISIBLE_DEVICES=$HIP_VISIBLE_DEVICES"'
                                         ' > %s\nprintf "%%s\\n" "$@" >> %s\n' % (self.calls, self.calls))
        r = self.run_rendered("server.sh", PORT="30001", HIP_VISIBLE_DEVICES="6,7", EXTRA_ARGS="--speculative-algorithm EAGLE")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.calls.read_text().splitlines(),
                         ["SGLANG_USE_AITER=1 HIP_VISIBLE_DEVICES=6,7", "serve", "--model-path", "/models/glm", "--tp", "4", "--host", "127.0.0.1",
                          "--port", "30001", "--mem-fraction-static", "0.80", "--chunked-prefill-size", "8192",
                          "--trust-remote-code", "--nsa-prefill-backend", "tilelang",
                          "--json-model-override-args", '{"index_topk_pattern": "FFSF"}', "--speculative-algorithm", "EAGLE"])

    def test_a_recipe_writes_nothing_into_the_script_but_quoted_words(self):
        pwned = self.tmp / "pwned"
        rc, msg = self.render("--scripts", "server", "--set", "MODEL_PATH=/models/q",
                              "--cookbook", "sglang serve --model-path 'q\ntouch %s' --trust-remote-code" % pwned)
        self.assertEqual(rc, 0, msg)
        self.assertEqual(self.run_rendered("server.sh", PORT="30001").returncode, 0)
        self.assertFalse(pwned.exists(), "a model name with a newline ran as a command")

    def test_a_configurator_command_and_what_is_no_recipe(self):
        rc, msg = self.render("--scripts", "server", "--set", "MODEL_PATH=/models/q",
                              "--cookbook", "python -m sglang.launch_server \\\n  --model Qwen/Qwen3-8B")
        self.assertEqual(rc, 0, msg)
        rec = self.record()
        self.assertEqual((rec["cookbook"]["model"], rec["cookbook"]["args"], rec["parameters"]["TP"]["source"]),
                         ("Qwen/Qwen3-8B", [], "template"))
        for text, what in (("docker run --rm lmsysorg/sglang sglang serve --model-path m", "not its docker command"),
                           ("pip install sglang", "no sglang launch command"),
                           ("cd /models && sglang serve --model-path m", "sets no variable"),
                           ("sglang serve m --tp 8", "comes before any flag")):
            rc, msg = self.render("--scripts", "server", "--set", "MODEL_PATH=/models/q", "--cookbook", text)
            self.assertEqual(rc, 2, text)
            self.assertIn(what, msg)

    def test_the_rendered_client_runs_each_concurrency_with_its_prompts_and_warm_up(self):
        self.assertEqual(self.render("--scripts", "perf", "--workload", '{"isl": 1024, "osl": 256, "conc": [1, 8]}')[0], 0)
        bench = self.tmp / "bench"
        r = self.run_rendered("perf.sh", PORT="30001", OUT_DIR=str(bench))
        self.assertEqual(r.returncode, 0, r.stderr)
        runs = self.calls.read_text().splitlines()
        self.assertEqual(len(runs), 2)
        last = runs[1].split()
        self.assertEqual(last[:2], ["-m", "sglang.benchmark.serving"])
        for flag, value in (("--random-input-len", "1024"), ("--random-output-len", "256"), ("--max-concurrency", "8"),
                            ("--num-prompts", "32"), ("--warmup-requests", "16"), ("--output-file", str(bench / "bench.jsonl"))):
            self.assertEqual(last[last.index(flag) + 1], value, flag)
        self.assertIn("--flush-cache", last)
        self.assertEqual(len(list(bench.glob("bench_1024_256_c*.log"))), 2)

    def test_a_benchmark_run_with_a_failed_request_is_no_measurement(self):
        self.assertEqual(self.render("--scripts", "perf", "--workload", '{"isl": 1024, "osl": 256, "conc": [1, 8]}')[0], 0)
        bench = self.tmp / "bench"
        r = self.run_rendered("perf.sh", PORT="30001", OUT_DIR=str(bench), FAKE_COMPLETED="3")
        self.assertEqual(r.returncode, 1, "the benchmark exits 0 when a request failed; perf.sh does not")
        self.assertIn("not every request completed", r.stderr)
        self.assertIn("3 of the 4 requests completed", "".join(f.read_text() for f in bench.glob("bench_*_c1_*.log")))
        self.assertEqual(self.run_rendered("perf.sh", PORT="30001", OUT_DIR=str(self.tmp / "again")).returncode, 0)

    def test_the_rendered_accuracy_script_runs_eval_accuracy_with_the_run_settings(self):
        self.assertEqual(self.render("--scripts", "accuracy", "--feature", json.dumps(self.FEATURE))[0], 0)
        self.assertIn('INFERPILOT_DIR="${INFERPILOT_DIR:-%s}"' % harness.ROOT, (self.out / "accuracy.sh").read_text())
        fake = self.tmp / "inferpilot"
        (fake / "scripts").mkdir(parents=True)
        (fake / "scripts" / "eval_accuracy.sh").write_text(
            'echo "sets=$* max_tokens=$MAX_TOKENS thinking=$ENABLE_THINKING effort=[$REASONING_EFFORT]" > %s\n' % self.calls)
        r = self.run_rendered("accuracy.sh", "gsm8k", "gpqa_diamond", INFERPILOT_DIR=str(fake), ENABLE_THINKING="1",
                              MAX_TOKENS="999")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.calls.read_text().strip(), "sets=gsm8k gpqa_diamond max_tokens=32768 thinking=0 effort=[]",
                         "the sets are its arguments, and an exported setting replaces no rendered one")
        self.assertEqual(self.run_rendered("accuracy.sh", INFERPILOT_DIR=str(fake)).returncode, 0)
        self.assertTrue(self.calls.read_text().startswith("sets= "), "without arguments, eval_accuracy.sh's own: gsm8k")

    def test_the_accuracy_template_exports_every_setting_and_thinking_brings_its_effort(self):
        text = (harness.TEMPLATES / "accuracy.sh").read_text()
        exported = re.search(r"^export (.+)$", text, re.M).group(1).split()
        self.assertEqual(sorted(exported), sorted(n for n, p in harness.catalog().items() if p["script"] == "accuracy"),
                         "what the run sets reaches eval_accuracy.sh for every arm")
        self.assertEqual(self.render("--scripts", "accuracy")[0], 0)
        p = self.record()["parameters"]
        self.assertEqual((p["MAX_TOKENS"]["value"], p["ENABLE_THINKING"]["value"], p["REASONING_EFFORT"]["value"]), ("32768", "1", "max"))
        self.assertEqual(self.render("--scripts", "accuracy", "--set", "ENABLE_THINKING=0")[0], 0)
        self.assertEqual(self.record()["parameters"]["REASONING_EFFORT"]["value"], "", "no reasoning effort without thinking")

    def test_the_accuracy_template_defaults_are_eval_accuracys(self):
        """Only the task or the feature's source changes how accuracy is measured."""
        own = dict(re.findall(r"^(MAX_TOKENS|ENABLE_THINKING|REASONING_EFFORT)=\$\{\1:?-([^}]*)\}$",
                              (SCRIPTS / "eval_accuracy.sh").read_text(), re.M))
        self.assertEqual(sorted(own), ["ENABLE_THINKING", "MAX_TOKENS", "REASONING_EFFORT"])
        cat = harness.catalog()
        self.assertEqual({n: cat[n]["default"] for n in own}, own)


class StabilityTest(unittest.TestCase):
    """scripts/stability.sh, G4's load, with a fake client and a fake DRM tree: the client run back to back,
    one line a pass, until the hours have passed or a pass fails."""
    SCRIPT = SCRIPTS / "stability.sh"

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.out = self.tmp / "logs" / "g4_feature"
        drm = self.tmp / "drm"
        for card, used in (("card1", "1000"), ("card2", "2000")):
            (drm / card / "device").mkdir(parents=True)
            (drm / card / "device" / "mem_info_vram_used").write_text(used + "\n")
        (drm / "card1-DP-1").mkdir()  # a connector, which holds no memory
        # The client appends its result to OUT_DIR/bench.jsonl, as perf.sh does; it fails in the pass FAIL_AT
        # names, and from its second pass on leaves OUT_DIR looking as old as a dead run's.
        self.client = self.tmp / "client.sh"
        self.client.write_text(textwrap.dedent("""\
            #!/bin/bash
            n=$(( $(cat "$OUT_DIR/passes" 2>/dev/null || echo 0) + 1 ))
            echo "$n" > "$OUT_DIR/passes"
            echo "pass $n on port $PORT at concurrency ${CONCURRENCY:-none}"
            sleep "${FAKE_SLEEP:-0}"
            [ "$n" -ge 2 ] && touch -d @0 "$OUT_DIR"
            [ "$n" = "${FAIL_AT:-}" ] && { echo "a request failed" >&2; exit 1; }
            echo "{\\"completed\\": 8, \\"output_throughput\\": $((100 + n))}" >> "$OUT_DIR/bench.jsonl"
        """))
        self.env = {"PATH": os.environ["PATH"], "PORT": "30001", "OUT_DIR": str(self.out), "DRM_ROOT": str(drm)}

    def run_script(self, *args, **env):
        env = {k: v for k, v in {**self.env, **env}.items() if v is not None}
        return subprocess.run(["bash", str(self.SCRIPT), *args], env=env, capture_output=True, text=True, timeout=120)

    def samples(self):
        return [json.loads(line) for line in (self.out / "stability.jsonl").read_text().splitlines()]

    def test_the_client_runs_back_to_back_until_the_hours_have_passed_one_line_a_pass(self):
        began = time.time()
        r = self.run_script(str(self.client), HOURS="0.001", CONCURRENCY="64", FAKE_SLEEP="0.3")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertGreaterEqual(time.time() - began, 0.001 * 3600 - 1, "it stopped before the hours had passed")
        got = self.samples()
        self.assertGreaterEqual(len(got), 2, "one pass is not back to back")
        self.assertEqual([s["pass"] for s in got], list(range(1, len(got) + 1)))
        self.assertEqual((self.out / "passes").read_text().strip(), str(len(got)), "a pass the client ran has no line")
        for s in got:
            self.assertEqual(s["exit"], 0)
            self.assertEqual(s["client"], [{"completed": 8, "output_throughput": 100 + s["pass"]}], "the client's result of this pass")
            self.assertEqual(s["vram_used"], {"card1": 1000, "card2": 2000})
            self.assertGreater(s["host_mem_used_kb"], 0)
            self.assertRegex(s["started"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
            self.assertGreaterEqual(s["seconds"], 0)
        log = (self.out / "stability.log").read_text()
        self.assertIn("=== pass 1, ", log)
        self.assertIn("pass 1 on port 30001 at concurrency 64", log, "the client runs with the port and the concurrency given")
        self.assertLess(time.time() - self.out.stat().st_mtime, 60, "OUT_DIR shows no life after the last pass")

    def test_the_first_pass_that_fails_ends_the_run(self):
        r = self.run_script(str(self.client), HOURS="1", FAIL_AT="2")
        self.assertEqual(r.returncode, 1)
        self.assertIn("pass 2: the client exited 1", r.stderr)
        got = self.samples()
        self.assertEqual([(s["pass"], s["exit"]) for s in got], [(1, 0), (2, 1)])
        self.assertEqual(got[1]["client"], [], "the failed pass appended no result")
        self.assertEqual((self.out / "passes").read_text().strip(), "2", "no pass after the one that failed")
        self.assertIn("a request failed", (self.out / "stability.log").read_text())

    def test_one_pass_runs_even_when_no_time_is_asked(self):
        r = self.run_script(str(self.client), HOURS="0")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual([s["pass"] for s in self.samples()], [1])

    def test_what_it_cannot_run_is_a_usage_error_and_starts_nothing(self):
        for args, env in (((), {}), ((str(self.tmp / "missing.sh"),), {}), ((str(self.client), "more"), {}),
                          ((str(self.client),), {"PORT": None}), ((str(self.client),), {"OUT_DIR": None}),
                          ((str(self.client),), {"HOURS": "soon"}), ((str(self.client),), {"HOURS": "-1"}),
                          ((str(self.client),), {"HOURS": "inf"})):
            r = self.run_script(*args, **env)
            self.assertEqual(r.returncode, 2, "%s %s: %s" % (args, env, r.stderr))
            self.assertFalse(self.out.exists(), "%s %s started a pass" % (args, env))

    def test_it_is_the_script_the_verify_engineer_is_given(self):
        self.assertEqual(subprocess.run(["bash", "-n", str(self.SCRIPT)]).returncode, 0)
        self.assertTrue(os.access(self.SCRIPT, os.X_OK))
        role = " ".join((SCRIPTS.parent / "roles" / "verify_engineer.md").read_text(encoding="utf-8").split())
        self.assertIn("bash <inferpilot_dir>/scripts/stability.sh <client_script>", role)
        for name in ("PORT", "OUT_DIR", "HOURS", "CONCURRENCY"):
            self.assertRegex(role, r"`%s[=`]" % name, "the role does not give stability.sh its %s" % name)


def git(*args, cwd=None):
    import subprocess
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=cwd, check=True,
                   capture_output=True, text=True)


class ProposeKnowledgeTest(unittest.TestCase):
    """The curator's knowledge/ changes as a pull request; a local bare repository stands in for GitHub."""

    def setUp(self):
        import subprocess
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        self.origin, seed, self.inferpilot = root / "origin.git", root / "seed", root / "inferpilot"
        git("init", "-q", "--bare", str(self.origin))
        git("clone", "-q", str(self.origin), str(seed))
        (seed / "knowledge").mkdir()
        (seed / "knowledge" / "known_issues.md").write_text("# Known issues\n")
        (seed / "inferpilot.js").write_text("// inferpilot\n")
        git("add", "-A", cwd=seed)
        git("commit", "-q", "-m", "seed", cwd=seed)
        git("push", "-q", "origin", "HEAD:refs/heads/main", cwd=seed)
        git("clone", "-q", "-b", "main", str(self.origin), str(self.inferpilot))
        self.run = root / "exp" / "issue_41299-glm-dcp_20261106-071502Z"
        self.run.mkdir(parents=True)
        self.body = self.run / "knowledge_pr.md"
        self.body.write_text("KI-001: GLM-5.3 with --dcp-size 8 on MI300X, from issue #41299.\n")
        self.show = lambda ref: subprocess.run(["git", "--git-dir", str(self.origin), "show", ref],
                                               capture_output=True, text=True).stdout

    def propose(self, token="t", api=None, extra=(), inferpilot=None):
        env = {k: v for k, v in os.environ.items() if k != "GITHUB_TOKEN"}
        if token:
            env["GITHUB_TOKEN"] = token
        with mock.patch.dict(os.environ, env, clear=True), redirect_stdout(io.StringIO()) as out, \
                redirect_stderr(io.StringIO()) as err, mock.patch.object(propose_knowledge, "api", side_effect=api):
            rc = propose_knowledge.main(["--run-dir", str(self.run), "--body", str(self.body), "--repo", "At1a8/inferpilot",
                                         "--inferpilot-dir", str(inferpilot or self.inferpilot), "--push-url", str(self.origin),
                                         *extra])
        return rc, out.getvalue(), err.getvalue()

    @staticmethod
    def github(calls):
        def fake(method, path, token="", data=None):
            calls.append((method, path, data))
            return [] if method == "GET" else {"number": 12, "html_url": "https://github.com/At1a8/inferpilot/pull/12"}
        return fake

    def test_a_known_issue_numbered_on_an_older_main_takes_the_next_free_number(self):
        other = self.origin.parent / "other"
        git("clone", "-q", "-b", "main", str(self.origin), str(other))
        (other / "knowledge" / "known_issues.md").write_text("# Known issues\n\n## KI-001 — aiter import segfault\nmain\n")
        git("commit", "-qam", "another run's known issue, merged meanwhile", cwd=other)
        git("push", "-q", "origin", "HEAD:refs/heads/main", cwd=other)
        (self.inferpilot / "knowledge" / "known_issues.md").write_text("# Known issues\n\n## KI-001 — GLM-5.3 dcp 8\nours\n")
        (self.inferpilot / "knowledge" / "dispatch.md").write_text("dcp 8: see KI-001\n")
        calls = []
        rc, out, err = self.propose(api=self.github(calls))
        self.assertEqual(rc, 0, err)
        self.assertEqual(json.loads(out)["renumbered"], {"KI-001": "KI-002"})
        branch = "inferpilot/knowledge/" + self.run.name
        known = self.show(branch + ":knowledge/known_issues.md")
        self.assertIn("## KI-001 — aiter import segfault\nmain\n\n## KI-002 — GLM-5.3 dcp 8\nours\n", known,
                      "main's entry kept, this run's after it with the next number")
        self.assertEqual(self.show(branch + ":knowledge/dispatch.md"), "dcp 8: see KI-002\n", "its references follow")
        body = calls[1][2]["body"]
        self.assertTrue(body.startswith("KI-002: GLM-5.3") and "KI-001 is KI-002 here" in body, body)

    def test_the_patch_outlives_the_checkout_and_is_proposed_later_from_another(self):
        (self.inferpilot / "knowledge" / "known_issues.md").write_text("# Known issues\n\n## KI-001 — GLM-5.3 dcp 8\n")
        saved = self.run / "knowledge.patch"
        rc, _, err = self.propose(token="")
        self.assertEqual(rc, 2)
        self.assertIn("--patch %s" % saved, err)
        self.assertIn("+## KI-001 — GLM-5.3 dcp 8", saved.read_text())
        shutil.rmtree(self.inferpilot)  # the task's container is gone, and the checkout with it
        fresh = self.origin.parent / "fresh"
        git("clone", "-q", "-b", "main", str(self.origin), str(fresh))
        calls = []
        rc, out, err = self.propose(api=self.github(calls), extra=["--patch", str(saved)], inferpilot=fresh)
        self.assertEqual(rc, 0, err)
        self.assertIn("## KI-001 — GLM-5.3 dcp 8", self.show("inferpilot/knowledge/%s:knowledge/known_issues.md" % self.run.name))
        self.assertEqual((fresh / "knowledge" / "known_issues.md").read_text(), "# Known issues\n", "that checkout is left as it is")
        outside = self.run / "outside.patch"
        outside.write_text(saved.read_text().replace("knowledge/known_issues.md", "inferpilot.js"))
        self.assertEqual(self.propose(extra=["--patch", str(outside)], inferpilot=fresh)[0], 1, "only knowledge/ goes in")

    def test_proposed_later_from_the_checkout_that_saved_it_that_checkout_reads_main_again(self):
        known = self.inferpilot / "knowledge" / "known_issues.md"
        known.write_text("# Known issues\n\n## KI-001 — GLM-5.3 dcp 8\n")
        saved = self.run / "knowledge.patch"
        self.assertEqual(self.propose(token="")[0], 2)
        rc, _, err = self.propose(api=self.github([]), extra=["--patch", str(saved)])
        self.assertEqual(rc, 0, err)
        self.assertEqual(known.read_text(), "# Known issues\n", "the proposed edits leave the checkout that held them")
        known.write_text("# Known issues\n\n## KI-001 — GLM-5.3 dcp 8\n\n## KI-002 — more\n")
        rc, _, err = self.propose(api=self.github([]), extra=["--patch", str(saved)])
        self.assertEqual(rc, 0, err)
        self.assertIn("KI-002 — more", known.read_text(), "edits beyond the saved patch stay")

    def test_a_saved_patch_reaching_outside_knowledge_by_any_path_is_not_pushed(self):
        import subprocess
        (self.inferpilot / "knowledge" / "known_issues.md").write_text("# Known issues\n\n## KI-001 — GLM-5.3 dcp 8\n")
        (self.inferpilot / "odd dir").mkdir()
        (self.inferpilot / "odd dir" / "x.sh").write_text("echo outside\n")
        git("add", "-N", "odd dir/x.sh", cwd=self.inferpilot)
        sneaky = self.run / "sneaky.patch"
        sneaky.write_text(subprocess.run(["git", "-C", str(self.inferpilot), "diff", "--binary"],
                                         capture_output=True, text=True, check=True).stdout)
        self.assertIn("diff --git a/odd dir/x.sh b/odd dir/x.sh", sneaky.read_text())
        git("reset", "-q", cwd=self.inferpilot)
        git("checkout", "-q", "--", "knowledge", cwd=self.inferpilot)
        shutil.rmtree(self.inferpilot / "odd dir")
        rc, _, err = self.propose(api=self.github([]), extra=["--patch", str(sneaky)])
        self.assertEqual(rc, 1, err)
        self.assertIn("odd dir/x.sh", err)
        self.assertEqual(self.show("inferpilot/knowledge/%s:knowledge/known_issues.md" % self.run.name), "", "nothing pushed")

    def _main_and_checkout_have(self, text):
        other = self.origin.parent / "other"
        if not other.exists():
            git("clone", "-q", "-b", "main", str(self.origin), str(other))
        (other / "knowledge" / "known_issues.md").write_text(text)
        git("commit", "-qam", "main", cwd=other)
        git("push", "-q", "origin", "HEAD:refs/heads/main", cwd=other)
        return other

    def test_an_entry_main_and_the_run_both_changed_stops_for_a_human(self):
        base = "# Known issues\n\n## KI-001 — aiter segfault\nStatus: open\n"
        other = self._main_and_checkout_have(base)
        git("pull", "-q", cwd=self.inferpilot)
        (other / "knowledge" / "known_issues.md").write_text(base.replace("open", "workaround: AITER_X=0"))
        git("commit", "-qam", "main changes the entry", cwd=other)
        git("push", "-q", "origin", "HEAD:refs/heads/main", cwd=other)
        (self.inferpilot / "knowledge" / "known_issues.md").write_text(base.replace("open", "fixed by #123"))
        rc, _, err = self.propose(api=self.github([]))
        self.assertEqual(rc, 4, err)
        self.assertIn("knowledge/known_issues.md", err)
        self.assertEqual(self.show("inferpilot/knowledge/%s:knowledge/known_issues.md" % self.run.name), "", "nothing pushed")

    def test_an_entry_the_run_changed_and_one_main_appended_are_both_kept(self):
        base = "# Known issues\n\n## KI-001 — aiter segfault\nStatus: open\n"
        other = self._main_and_checkout_have(base)
        git("pull", "-q", cwd=self.inferpilot)
        (other / "knowledge" / "known_issues.md").write_text(base + "\n## KI-002 — main's\nmain\n")
        git("commit", "-qam", "main appends", cwd=other)
        git("push", "-q", "origin", "HEAD:refs/heads/main", cwd=other)
        (self.inferpilot / "knowledge" / "known_issues.md").write_text(
            base.replace("open", "fixed by #123") + "\n## KI-002 — GLM-5.3 dcp 8\nours\n")
        rc, out, err = self.propose(api=self.github([]))
        self.assertEqual(rc, 0, err)
        self.assertEqual(json.loads(out)["renumbered"], {"KI-002": "KI-003"})
        known = self.show("inferpilot/knowledge/%s:knowledge/known_issues.md" % self.run.name)
        for want in ("Status: fixed by #123\n", "## KI-002 — main's\nmain\n", "## KI-003 — GLM-5.3 dcp 8\nours\n"):
            self.assertIn(want, known)
        self.assertNotIn("Status: open", known)
        self.assertNotIn("<<<<<<<", known)

    def test_without_the_file_it_was_made_against_the_patch_itself_tells_its_new_known_issues(self):
        diff = ("diff --git a/knowledge/known_issues.md b/knowledge/known_issues.md\n"
                "index %s..%s 100644\n--- a/knowledge/known_issues.md\n+++ b/knowledge/known_issues.md\n"
                "@@ -1,4 +1,7 @@\n # Known issues\n \n-## KI-001 — aiter segfault\n+## KI-001 — aiter import segfault\n main\n"
                "+\n+## KI-002 — GLM-5.3 dcp 8\n+see KI-001 and KI-002\n" % ("1" * 40, "2" * 40))
        self.assertEqual(propose_knowledge.preimage(str(self.inferpilot), diff, propose_knowledge.KNOWN), "",
                         "a blob this checkout lacks: not its HEAD, which may hold main's entries")
        main = "# Known issues\n\n## KI-001 — aiter segfault\nmain\n\n## KI-002 — another run's\n"
        out, mapping = propose_knowledge.renumber(diff, "", main)
        self.assertEqual(mapping, {"KI-002": "KI-003"}, "the KI-001 the patch edits is main's own")
        self.assertIn("+## KI-001 — aiter import segfault\n", out)
        self.assertIn("+## KI-003 — GLM-5.3 dcp 8\n+see KI-001 and KI-003\n", out)

    def test_only_knowledge_goes_to_its_branch_and_the_checkout_reads_main_again(self):
        (self.inferpilot / "knowledge" / "known_issues.md").write_text("# Known issues\nKI-001 GLM-5.3 dcp 8\n")
        (self.inferpilot / "knowledge" / "feature_skills" / "dcp").mkdir(parents=True)
        (self.inferpilot / "knowledge" / "feature_skills" / "dcp" / "SKILL.md").write_text("draft\n")
        (self.inferpilot / "inferpilot.js").write_text("// a local change that is not knowledge\n")
        calls = []

        def fake(method, path, token="", data=None):
            calls.append((method, path, data))
            return [] if method == "GET" else {"number": 12, "html_url": "https://github.com/At1a8/inferpilot/pull/12"}

        rc, out, err = self.propose(api=fake)
        self.assertEqual(rc, 0, err)
        self.assertEqual(json.loads(out)["url"], "https://github.com/At1a8/inferpilot/pull/12")
        branch = "inferpilot/knowledge/" + self.run.name
        self.assertIn("KI-001", self.show(branch + ":knowledge/known_issues.md"))
        self.assertEqual(self.show(branch + ":knowledge/feature_skills/dcp/SKILL.md"), "draft\n")
        self.assertEqual(self.show(branch + ":inferpilot.js"), "// inferpilot\n")
        self.assertEqual(self.show("main:knowledge/known_issues.md"), "# Known issues\n", "main is never pushed")
        self.assertEqual((calls[1][0], calls[1][2]["head"], calls[1][2]["base"]), ("POST", branch, "main"))
        self.assertEqual((self.inferpilot / "knowledge" / "known_issues.md").read_text(), "# Known issues\n")
        self.assertFalse((self.inferpilot / "knowledge" / "feature_skills").exists())
        self.assertIn("a local change", (self.inferpilot / "inferpilot.js").read_text())

    def test_nothing_leaves_without_a_token_with_a_leak_or_without_changes(self):
        self.assertEqual(self.propose()[0:2], (0, '{"action": "none", "reason": "no changes under knowledge/"}\n'))
        (self.inferpilot / "knowledge" / "known_issues.md").write_text("# Known issues\nKI-001 GLM-5.3 dcp 8\n")
        self.assertEqual(self.propose(token="")[0], 2)
        (self.inferpilot / "knowledge" / "known_issues.md").write_text("# Known issues\nweights in /data/models/x\n")
        self.assertEqual(self.propose()[0], 3)
        self.assertIn("/data/models/x", (self.inferpilot / "knowledge" / "known_issues.md").read_text(), "left as it was")
        self.assertEqual(self.show("inferpilot/knowledge/" + self.run.name), "", "no branch was pushed")

    def test_a_change_that_names_this_machine_is_not_saved_for_later(self):
        known = self.inferpilot / "knowledge" / "known_issues.md"
        known.write_text("# Known issues\nweights in /data/models/x\n")
        self.assertEqual(self.propose(token="")[0], 3)
        self.assertFalse((self.run / "knowledge.patch").exists(), "saved for a --patch elsewhere, whose check knows nothing of here")
        known.write_text("# Known issues\nKI-001 GLM-5.3 dcp 8\n")
        self.body.write_text("found with /data/models/x\n")
        self.assertEqual(self.propose(token="")[0], 3)
        self.assertIn("KI-001 GLM-5.3 dcp 8", (self.run / "knowledge.patch").read_text(), "a clean change is kept while its description is fixed")

    def test_a_file_whose_name_is_this_machines_is_refused_as_a_line_that_names_it_is(self):
        with mock.patch.object(propose_knowledge, "this_host", return_value="gpu-node-17"):
            (self.inferpilot / "knowledge" / "gpu-node-17.md").write_text("notes on dcp 8\n")
            rc, _, err = self.propose(api=self.github([]))
            self.assertEqual(rc, 3, err)
            self.assertIn("path 1: knowledge/gpu-node-17.md", err)
            self.assertFalse((self.run / "knowledge.patch").exists(), "its path is published as its lines are")
            git("mv", "knowledge/known_issues.md", "knowledge/gpu-node-18.md", cwd=self.inferpilot)
            (self.inferpilot / "knowledge" / "gpu-node-17.md").unlink()
            rc, _, err = self.propose(api=self.github([]))
            self.assertEqual(rc, 3, "a rename to a sibling's name: %s" % err)
        self.assertEqual(self.show("inferpilot/knowledge/" + self.run.name), "", "no branch was pushed")

    def test_an_added_line_that_begins_with_two_pluses_is_checked_and_a_binary_file_is_not_proposed(self):
        known = self.inferpilot / "knowledge" / "known_issues.md"
        known.write_text("# Known issues\n-- not a header either\n++ weights in /data/models/x\n")
        self.assertEqual(propose_knowledge.added("--- a/k\n+++ b/k\n@@ -1,2 +1,2 @@\n ctx\n--- gone\n+++ here\n"
                                                 "diff --git a/l b/l\n--- a/l\n+++ b/l\n@@ -0,0 +1 @@\n+new\n"),
                         "++ here\nnew", "inside a hunk +++ is an added line, and outside it a header")
        rc, _, err = self.propose()
        self.assertEqual(rc, 3, err)
        self.assertIn("/data/models/x", err)
        known.write_text("# Known issues\n")
        (self.inferpilot / "knowledge" / "trace.bin").write_bytes(b"\x00\x01/data/models/x\x00" * 40)
        rc, _, err = self.propose()
        self.assertEqual(rc, 1, err)
        self.assertIn("a binary file, whose contents cannot be checked", err)
        self.assertFalse((self.run / "knowledge.patch").exists())
        self.assertEqual(self.show("inferpilot/knowledge/" + self.run.name), "", "no branch was pushed")

    def test_a_description_that_names_the_machine_the_patch_was_made_on_is_refused_on_another(self):
        (self.inferpilot / "knowledge" / "known_issues.md").write_text("# Known issues\n\n## KI-001 — GLM-5.3 dcp 8\n")
        saved = self.run / "knowledge.patch"
        with mock.patch.object(propose_knowledge, "this_host", return_value="gpu-node-17"):
            self.body.write_text("KI-001: seen on gpu-node-17 with dcp 8\n")
            self.assertEqual(self.propose(token="")[0], 3, "the description names the machine")
        self.assertTrue(saved.read_text().startswith("origin gpu-node-17\n"), "the clean change is kept, with where it was made")
        with mock.patch.object(propose_knowledge, "this_host", return_value="laptop"):
            rc, _, err = self.propose(api=self.github([]), extra=["--patch", str(saved)])
            self.assertEqual(rc, 3, err)
            self.assertIn("gpu-node-17", err)
            self.assertEqual(self.show("inferpilot/knowledge/" + self.run.name), "", "no branch was pushed")
            self.body.write_text("KI-001: seen with dcp 8\n")
            rc, _, err = self.propose(api=self.github([]), extra=["--patch", str(saved)])
            self.assertEqual(rc, 0, err)
        self.assertNotIn("gpu-node-17", self.show("inferpilot/knowledge/" + self.run.name), "the name stays in the run's file")

    def test_a_path_that_is_local_where_the_patch_was_made_is_refused_where_it_is_not(self):
        (self.inferpilot / "knowledge" / "known_issues.md").write_text("# Known issues\n\n## KI-001 — GLM-5.3 dcp 8\n")
        saved = self.run / "knowledge.patch"
        self.body.write_text("KI-001: weights in /fastvault/private-model, dcp 8\n")
        # the machine the run is on mounts storage on /fastvault
        with mock.patch.object(propose_knowledge, "mounted_roots", return_value=("fastvault",)), \
                mock.patch.object(daily_report, "LOCAL", daily_report.local_paths(("fastvault",))):
            self.assertEqual(self.propose(token="")[0], 3, "the description names a path of the machine")
        self.assertIn('\nmounts ["fastvault"]\n', "\n" + saved.read_text(), "the clean change is kept, with what is local there")
        # the machine it is proposed from does not
        self.assertEqual(daily_report.leaks(self.body.read_text()), [], "the test meant a path that is not local here")
        rc, _, err = self.propose(api=self.github([]), extra=["--patch", str(saved)])
        self.assertEqual(rc, 3, err)
        self.assertIn("/fastvault/private-model", err)
        self.assertEqual(self.show("inferpilot/knowledge/" + self.run.name), "", "no branch was pushed")
        odd = self.run / "odd.patch"
        odd.write_text(saved.read_text().replace('mounts ["fastvault"]', "mounts fastvault"))
        rc, _, err = self.propose(api=self.github([]), extra=["--patch", str(odd)])
        self.assertEqual(rc, 1, "what it cannot read of the patch's machine is not passed over: %s" % err)
        self.assertIn("mounts line", err)
        self.body.write_text("KI-001: dcp 8\n")
        rc, _, err = self.propose(api=self.github([]), extra=["--patch", str(saved)])
        self.assertEqual(rc, 0, err)
        self.assertNotIn("fastvault", self.show("inferpilot/knowledge/" + self.run.name), "the mounts stay in the run's file")

    def test_a_file_of_the_run_named_after_a_known_issue_keeps_its_name_in_the_description(self):
        body = ("KI-001: dcp 8 (KI-001). Proof: spinoff/KI-001.md, [KI-001](spinoff/KI-001.md), KI-001.json,\n"
                "KI-001-draft.md and notes_KI-001.md. See KI-001.\n")
        out = propose_knowledge.with_renumbering(body, {"KI-001": "KI-004"})
        self.assertTrue(out.startswith(
            "KI-004: dcp 8 (KI-004). Proof: spinoff/KI-001.md, [KI-004](spinoff/KI-001.md), KI-001.json,\n"
            "KI-001-draft.md and notes_KI-001.md. See KI-004.\n"), out)
        self.assertIn("KI-001 is KI-004 here; the run's own files keep KI-001", out)

    def test_in_the_knowledge_lines_a_file_keeps_its_name_and_a_line_of_two_pluses_its_reference(self):
        self._main_and_checkout_have("# Known issues\n\n## KI-001 — aiter import segfault\nmain\n")
        (self.inferpilot / "knowledge" / "known_issues.md").write_text(
            "# Known issues\n\n## KI-001 — GLM-5.3 dcp 8\nReport: [KI-001](spinoff/KI-001.md), logs/KI-001/server.log;"
            " a KI-001-related hang, as KI-001/KI-001 would be.\n")
        (self.inferpilot / "knowledge" / "dispatch.md").write_text("++ see KI-001\n-- and KI-001.\ndraft: KI-001-draft.md\n")
        rc, out, err = self.propose(api=self.github([]))
        self.assertEqual(rc, 0, err)
        self.assertEqual(json.loads(out)["renumbered"], {"KI-001": "KI-002"})
        branch = "inferpilot/knowledge/" + self.run.name
        self.assertIn("## KI-002 — GLM-5.3 dcp 8\nReport: [KI-002](spinoff/KI-001.md), logs/KI-001/server.log;"
                      " a KI-002-related hang, as KI-002/KI-002 would be.\n", self.show(branch + ":knowledge/known_issues.md"),
                      "the issue takes its new number where it is named, and a file of the run keeps the name it has")
        self.assertEqual(self.show(branch + ":knowledge/dispatch.md"), "++ see KI-002\n-- and KI-002.\ndraft: KI-001-draft.md\n",
                         "an added line that begins with ++ is not a file header: its reference follows")

    def test_a_patch_without_the_file_it_was_made_against_is_not_applied_where_the_checkout_lacks_it_too(self):
        import subprocess
        base = "# Known issues\n\n## KI-001 — aiter segfault\nStatus: open\nWorkaround: none\n"
        other = self._main_and_checkout_have(base)
        git("pull", "-q", cwd=self.inferpilot)
        (self.inferpilot / "knowledge" / "known_issues.md").write_text(base.replace("open", "fixed by #9"))
        saved = self.run / "knowledge.patch"
        self.assertEqual(self.propose(token="")[0], 2)
        bare = self.run / "bare.patch"
        bare.write_text("".join(l for l in saved.read_text().splitlines(keepends=True) if not l.startswith("preimage ")))
        self.assertNotEqual(bare.read_text(), saved.read_text(), "the test meant a patch without its preimage")
        shutil.rmtree(self.inferpilot)
        (other / "knowledge" / "known_issues.md").write_text(base.replace("none", "AITER_X=0"))
        git("commit", "-qam", "main changes another line of the entry the patch changes", cwd=other)
        git("push", "-q", "origin", "HEAD:refs/heads/main", cwd=other)
        shallow = self.origin.parent / "shallow"
        git("clone", "-q", "--depth=1", "-b", "main", "file://" + str(self.origin), str(shallow))
        rc, _, err = self.propose(api=self.github([]), extra=["--patch", str(bare)], inferpilot=shallow)
        self.assertEqual(rc, 4, err)
        self.assertIn("neither in the patch nor in this checkout", err)
        self.assertEqual(self.show("inferpilot/knowledge/" + self.run.name), "", "no branch was pushed")
        rc, _, err = self.propose(api=self.github([]), extra=["--patch", str(saved)], inferpilot=shallow)
        self.assertEqual(rc, 4, "with the preimage the entry both changed is seen: %s" % err)
        self.assertIn("KI-001 changed on main as well", err)

    def test_a_change_that_is_refused_leaves_no_earlier_patch_as_its_own_and_a_dry_run_writes_none(self):
        known = self.inferpilot / "knowledge" / "known_issues.md"
        saved = self.run / "knowledge.patch"
        known.write_text("# Known issues\n\n## KI-001 — GLM-5.3 dcp 8\n")
        rc, out, err = self.propose(extra=["--dry-run"])
        self.assertEqual((rc, json.loads(out)["patch"]), (0, ""), err)
        self.assertFalse(saved.exists(), "a dry run writes nothing")
        self.assertEqual(self.propose(token="")[0], 2)
        first = saved.read_text()
        known.write_text("# Known issues\n\n## KI-001 — GLM-5.3 dcp 8\n\n## KI-002 — other\n")
        self.assertEqual(self.propose(extra=["--dry-run"])[0], 0)
        self.assertEqual(saved.read_text(), first, "a dry run leaves the patch to propose later as it was")
        known.write_text("# Known issues\n\n## KI-001 — GLM-5.3 dcp 8\nweights in /data/models/x\n")
        self.assertEqual(self.propose(extra=["--dry-run"])[0], 3)
        self.assertEqual(saved.read_text(), first, "refused in a dry run too, which still writes nothing")
        rc, _, err = self.propose(token="")
        self.assertEqual(rc, 3)
        self.assertFalse(saved.exists(), "the patch of the earlier change is not this one's")
        self.assertEqual((self.run / "knowledge.patch.earlier").read_text(), first)
        self.assertIn("knowledge.patch.earlier", err)

    def test_a_change_no_patch_can_be_made_of_leaves_no_earlier_patch_as_its_own(self):
        known = self.inferpilot / "knowledge" / "known_issues.md"
        saved, earlier = self.run / "knowledge.patch", self.run / "knowledge.patch.earlier"
        known.write_text("# Known issues\n\n## KI-001 — GLM-5.3 dcp 8\n")
        rc, _, err = self.propose(api=self.github([]), extra=["--push-url", str(self.origin.parent / "no-such-remote.git")])
        self.assertNotIn(rc, (0, 3, 4), err)
        first = saved.read_text()
        self.assertIn("+## KI-001 — GLM-5.3 dcp 8", first, "a clean change whose push failed is kept: it is this change")
        self.assertFalse(earlier.exists())
        (self.inferpilot / "knowledge" / "notes.md").write_bytes(b"caf\xe9 at dcp 8\n")
        rc, _, err = self.propose(api=self.github([]))
        self.assertEqual(rc, 1, err)
        self.assertIn("knowledge/notes.md is not UTF-8 text", err)
        self.assertFalse(saved.exists(), "the patch of the earlier change is not this one's")
        self.assertEqual(earlier.read_text(), first)
        self.assertIn("knowledge.patch.earlier", err)
        self.assertEqual(self.show("inferpilot/knowledge/" + self.run.name), "", "no branch was pushed")
        self.assertEqual(self.propose(extra=["--dry-run"])[0], 1)
        self.assertEqual(earlier.read_text(), first, "a dry run moves nothing")

    def test_the_command_to_propose_later_is_quoted_and_names_the_script_in_a_checkout(self):
        (self.inferpilot / "knowledge" / "known_issues.md").write_text("# Known issues\n\n## KI-001 — GLM-5.3 dcp 8\n")
        spaced = self.run.parent / "my runs" / "issue_1; echo x"
        spaced.mkdir(parents=True)
        body = spaced / "knowledge_pr.md"
        body.write_text("KI-001: dcp 8\n")
        argv = ["--run-dir", str(spaced), "--body", str(body), "--inferpilot-dir", str(self.inferpilot)]
        env = {k: v for k, v in os.environ.items() if k != "GITHUB_TOKEN"}
        with mock.patch.dict(os.environ, env, clear=True), redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()) as err, \
                mock.patch.object(sys, "argv", ["gone/with/the/container/propose_knowledge.py"]):
            self.assertEqual(propose_knowledge.main(argv), 2)
        command = err.getvalue().strip().splitlines()[-1].strip()
        self.assertEqual(shlex.split(command), ["python3", "scripts/propose_knowledge.py", "--run-dir", str(spaced), "--body",
                                                str(body), "--patch", str(spaced / "knowledge.patch")], command)
        self.assertIn("from any InferPilot checkout", err.getvalue())

    def test_a_patch_that_makes_a_symbolic_link_is_refused_and_nothing_is_written_through_it(self):
        import subprocess
        victim = self.origin.parent / "victim.md"
        victim.write_text("x\n## KI-001 elsewhere\n")
        known = self.inferpilot / "knowledge" / "known_issues.md"
        known.unlink()
        # Relative, so that no leak check stops it first; from the worktree beside the victim it reaches the victim.
        known.symlink_to(os.path.join("..", "..", "victim.md"))
        git("add", "-A", "knowledge", cwd=self.inferpilot)
        link = self.run / "link.patch"
        link.write_text(subprocess.run(["git", "-C", str(self.inferpilot), "diff", "--cached", "--binary"],
                                       capture_output=True, text=True, check=True).stdout)
        git("reset", "-q", cwd=self.inferpilot)
        fresh = self.origin.parent / "fresh"
        git("clone", "-q", "-b", "main", str(self.origin), str(fresh))
        with mock.patch.object(tempfile, "tempdir", str(self.origin.parent)):
            rc, _, err = self.propose(api=self.github([]), extra=["--patch", str(link)], inferpilot=fresh)
            self.assertEqual(rc, 1, err)
            self.assertIn("symbolic link", err)
            self.assertEqual(self.propose(api=self.github([]))[0], 1, "nor the checkout's own")
        self.assertEqual(victim.read_text(), "x\n## KI-001 elsewhere\n", "a file outside the worktree was written")
        self.assertEqual(self.show("inferpilot/knowledge/%s:knowledge/known_issues.md" % self.run.name), "", "nothing pushed")

    def test_a_name_with_a_space_or_one_git_quotes_is_read_as_git_reads_it(self):
        names = ["knowledge/notes with spaces.md", 'knowledge/na\u00efve "quoted".md']
        for name in names:
            (self.inferpilot / name).write_text("a note\n")
        saved = self.run / "knowledge.patch"
        self.assertEqual(self.propose(token="")[0], 2)
        fresh = self.origin.parent / "fresh"
        git("clone", "-q", "-b", "main", str(self.origin), str(fresh))
        rc, out, err = self.propose(api=self.github([]), extra=["--patch", str(saved)], inferpilot=fresh)
        self.assertEqual(rc, 0, err)
        self.assertEqual(sorted(json.loads(out)["files"]), sorted(names))
        for name in names:
            self.assertEqual(self.show("inferpilot/knowledge/%s:%s" % (self.run.name, name)), "a note\n", name)
        moved = self.run / "moved.patch"
        moved.write_text("diff --git a/inferpilot.js b/knowledge/x.js\nsimilarity index 100%\n"
                         "rename from inferpilot.js\nrename to knowledge/x.js\n")
        rc, _, err = self.propose(api=self.github([]), extra=["--patch", str(moved)], inferpilot=fresh)
        self.assertEqual(rc, 1, err)
        self.assertIn("is not a patch of knowledge/: it touches inferpilot.js", err, "a rename's source counts as a path it touches")
        quoted = ('diff --git "a/knowledge/na\\303\\257ve.md" b/knowledge/plain.md\nsimilarity index 100%\n'
                  'rename from "knowledge/na\\303\\257ve.md"\nrename to knowledge/plain.md\n')
        self.assertEqual(propose_knowledge.patched(quoted), ["knowledge/na\u00efve.md", "knowledge/plain.md"])

    def _entry_changed_on_main_and_here(self, base, mains, ours):
        other = self._main_and_checkout_have(base)
        git("pull", "-q", cwd=self.inferpilot)
        (other / "knowledge" / "known_issues.md").write_text(mains)
        git("commit", "-qam", "main changes the entry", cwd=other)
        git("push", "-q", "origin", "HEAD:refs/heads/main", cwd=other)
        (self.inferpilot / "knowledge" / "known_issues.md").write_text(ours)
        return self.propose(api=self.github([]))

    def test_lines_both_added_inside_one_entry_stop_for_a_human(self):
        base = "# Known issues\n\n## KI-001 — aiter segfault\nStatus: open\n\n## KI-002 — other\nx\n"
        rc, _, err = self._entry_changed_on_main_and_here(base, base.replace("open\n", "open\nWorkaround: disable A\n"),
                                                          base.replace("open\n", "open\nWorkaround: enable A\n"))
        self.assertEqual(rc, 4, err)
        self.assertIn("KI-001", err)
        self.assertEqual(self.show("inferpilot/knowledge/%s:knowledge/known_issues.md" % self.run.name), "", "nothing pushed")
        inside = ("## KI-001 — aiter segfault\nStatus: open\n<<<<<<< ours\nWorkaround: disable A\n||||||| base\n=======\n"
                  "Workaround: enable A\n>>>>>>> theirs\n\n## KI-002 — other\nx\n")
        self.assertIsNone(propose_knowledge.resolve_appends(inside), "lines added inside an entry are no new entries")
        at_end = ("## KI-001 — a\nx\n<<<<<<< ours\n\n## KI-002 — main's\n||||||| base\n=======\n\n## KI-003 — ours\n"
                  ">>>>>>> theirs\n")
        self.assertEqual(propose_knowledge.resolve_appends(at_end), "## KI-001 — a\nx\n\n## KI-002 — main's\n\n## KI-003 — ours\n")

    def test_different_lines_of_one_entry_changed_on_both_sides_stop_for_a_human(self):
        base = ("# Known issues\n\n## KI-001 — aiter segfault\nSymptom: crash at import\nReproduction: import aiter\n"
                "Status: open\n")
        rc, _, err = self._entry_changed_on_main_and_here(base, base.replace("at import", "at import, MI308X only"),
                                                          base.replace("open", "fixed by #123"))
        self.assertEqual(rc, 4, err)
        self.assertIn("KI-001", err)
        self.assertEqual(self.show("inferpilot/knowledge/%s:knowledge/known_issues.md" % self.run.name), "", "nothing pushed")

    def test_a_shallow_checkout_of_a_later_main_applies_the_patch_three_way(self):
        import subprocess
        base = "# Known issues\n\n## KI-001 — aiter segfault\nWorkaround: none\n\n## KI-002 — other\nx\n"
        other = self._main_and_checkout_have(base)
        git("pull", "-q", cwd=self.inferpilot)
        made_against = subprocess.run(["git", "-C", str(self.inferpilot), "rev-parse", "HEAD:knowledge/known_issues.md"],
                                      capture_output=True, text=True, check=True).stdout.strip()
        (self.inferpilot / "knowledge" / "known_issues.md").write_text(base.replace("\nx\n", "\nx, fixed by #9\n"))
        saved = self.run / "knowledge.patch"
        self.assertEqual(self.propose(token="")[0], 2)
        shutil.rmtree(self.inferpilot)
        (other / "knowledge" / "known_issues.md").write_text(base.replace("none", "AITER_X=0"))
        git("commit", "-qam", "main changes a line of the patch's context", cwd=other)
        git("push", "-q", "origin", "HEAD:refs/heads/main", cwd=other)
        shallow = self.origin.parent / "shallow"
        git("clone", "-q", "--depth=1", "-b", "main", "file://" + str(self.origin), str(shallow))
        self.assertNotEqual(subprocess.run(["git", "-C", str(shallow), "cat-file", "-e", made_against]).returncode, 0,
                            "the test meant a checkout without the file the patch was made against")
        rc, _, err = self.propose(api=self.github([]), extra=["--patch", str(saved)], inferpilot=shallow)
        self.assertEqual(rc, 0, err)
        known = self.show("inferpilot/knowledge/%s:knowledge/known_issues.md" % self.run.name)
        self.assertIn("Workaround: AITER_X=0\n", known)
        self.assertIn("\nx, fixed by #9\n", known)


class RunLockTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.lock = str(self.root / "inferpilot-run.lock")
        self.a, self.b = str(self.root / "run_a"), str(self.root / "run_b")
        Path(self.a).mkdir()

    def test_one_task_at_a_time(self):
        self.assertEqual(run_lock.status(self.lock)["status"], "free")
        self.assertEqual(run_lock.acquire(self.lock, self.a, "issue 41299", "issue")["status"], "acquired")
        self.assertEqual(run_lock.acquire(self.lock, self.a)["status"], "acquired")  # a retry of the same run
        busy = run_lock.acquire(self.lock, self.b, "scan", "scan")
        self.assertEqual((busy["status"], busy["holder"]["run_dir"], busy["holder"]["task"]), ("busy", self.a, "issue 41299"))
        self.assertEqual(run_lock.release(self.lock, self.b)["status"], "held")
        self.assertEqual(run_lock.release(self.lock, self.a)["status"], "released")
        self.assertEqual(run_lock.acquire(self.lock, self.b)["status"], "acquired")

    def test_a_finished_or_silent_run_is_taken_over(self):
        t0 = 1_000_000.0
        run_lock.acquire(self.lock, self.a, idle_hours=12, now=t0)
        (Path(self.a) / "run_report.md").write_text("done")
        os.utime(Path(self.a) / "run_report.md", (t0 + 60, t0 + 60))
        took = run_lock.acquire(self.lock, self.b, now=t0 + 120)
        self.assertEqual((took["status"], took["took_over"]["reason"]), ("acquired", "its run finished"))
        Path(self.b).mkdir()
        os.utime(self.b, (t0 + 120, t0 + 120))
        self.assertEqual(run_lock.acquire(self.lock, self.a, now=t0 + 11 * 3600)["status"], "busy")
        late = run_lock.acquire(self.lock, self.a, now=t0 + 13 * 3600)
        self.assertEqual((late["status"], late["took_over"]["reason"]), ("acquired", "its run wrote nothing for 12 h"))

    def test_an_old_report_does_not_free_a_resumed_run(self):
        (Path(self.a) / "run_report.md").write_text("stopped at Design")
        os.utime(Path(self.a) / "run_report.md", (1000.0, 1000.0))
        run_lock.acquire(self.lock, self.a, now=2000.0)
        os.utime(self.a, (2000.0, 2000.0))
        self.assertEqual(run_lock.acquire(self.lock, self.b, now=2100.0)["status"], "busy")

    def test_a_run_in_another_container_is_not_taken_over_for_silence(self):
        """Its run directory is in that container: invisible here, so silence proves nothing."""
        other = str(self.root / "elsewhere" / "issue_41299-glm-dcp_20261106-071502Z")
        run_lock.acquire(self.lock, other, idle_hours=12, now=1000.0)
        self.assertEqual(run_lock.acquire(self.lock, self.a, now=1000.0 + 48 * 3600)["status"], "busy")
        self.assertEqual(run_lock.release(self.lock, force=True)["status"], "released")
        self.assertEqual(run_lock.acquire(self.lock, self.a)["status"], "acquired")

    @unittest.skipUnless(os.path.exists("/proc/self/stat"), "needs /proc")
    def test_the_lock_of_a_runner_that_is_gone_is_taken_over_at_once(self):
        with open("/proc/self/stat") as f:
            stat = f.read()
        me = "%d-%s" % (os.getpid(), stat[stat.rindex(")") + 2:].split(" ")[19])
        with mock.patch.dict(os.environ, {"INFERPILOT_RUN_TAG": me}):
            run_lock.acquire(self.lock, self.a, now=1000.0)
        os.utime(self.a, (1000.0, 1000.0))
        self.assertEqual(run_lock.status(self.lock, now=1100.0)["runner"], "running")
        self.assertEqual(run_lock.acquire(self.lock, self.b, now=1100.0)["status"], "busy")
        self.assertEqual(run_lock.release(self.lock, run_tag="1-1")["status"], "held")
        self.assertEqual(run_lock.release(self.lock, run_tag=me)["status"], "released")
        with mock.patch.dict(os.environ, {"INFERPILOT_RUN_TAG": "%d-1" % os.getpid()}):  # this pid, another start
            run_lock.acquire(self.lock, self.a, now=1000.0)
        self.assertEqual(run_lock.status(self.lock, now=1100.0)["runner"], "gone")
        took = run_lock.acquire(self.lock, self.b, now=1100.0)
        self.assertEqual((took["status"], took["took_over"]["reason"]), ("acquired", "its runner is gone"))

    @unittest.skipUnless(os.path.exists("/proc/self/stat"), "needs /proc")
    def test_a_run_resumed_by_another_runner_is_that_runners(self):
        with open("/proc/self/stat") as f:
            stat = f.read()
        me = "%d-%s" % (os.getpid(), stat[stat.rindex(")") + 2:].split(" ")[19])
        with mock.patch.dict(os.environ, {"INFERPILOT_RUN_TAG": "%d-1" % os.getpid()}):  # a runner that is gone
            run_lock.acquire(self.lock, self.a, now=1000.0)
        with mock.patch.dict(os.environ, {"INFERPILOT_RUN_TAG": me}):
            self.assertEqual(run_lock.acquire(self.lock, self.a, now=1100.0)["holder"]["run_tag"], me)
        os.utime(self.a, (1100.0, 1100.0))
        self.assertEqual(run_lock.acquire(self.lock, self.b, now=1200.0)["status"], "busy")
        with mock.patch.dict(os.environ, {"INFERPILOT_RUN_TAG": "1-1"}):  # a second runner for the same run
            self.assertEqual(run_lock.acquire(self.lock, self.a, now=1200.0)["status"], "busy")
        self.assertEqual(run_lock.release(self.lock, run_tag=me)["status"], "released")

    @unittest.skipUnless(os.path.exists("/proc/self/stat"), "needs /proc")
    def test_a_zombie_runner_is_gone(self):
        pid = os.fork()
        if pid == 0:
            os._exit(0)
        self.addCleanup(os.waitpid, pid, 0)
        for _ in range(100):  # until it has exited, unreaped
            with open("/proc/%d/stat" % pid) as f:
                fields = f.read().rsplit(")", 1)[1].split()
            if fields[0] == "Z":
                break
            time.sleep(0.01)
        self.assertEqual(fields[0], "Z")
        self.assertFalse(run_lock.runner_alive("%d-%s" % (pid, fields[19])))

    def test_a_runner_in_another_pid_namespace_counts_as_running(self):
        self.assertTrue(run_lock.runner_alive("999999999-1-1"))
        self.assertFalse(run_lock.runner_alive("999999999-1"))
        self.assertFalse(run_lock.runner_alive("999999999-1-%s" % run_lock.pid_ns().strip("pid:[]")))

    def test_a_runner_in_another_pid_namespace_is_not_judged(self):
        with mock.patch.dict(os.environ, {"INFERPILOT_RUN_TAG": "999999999-1"}), \
                mock.patch.object(run_lock, "pid_ns", return_value="pid:[1]"):
            run_lock.acquire(self.lock, self.a, now=1000.0)
        os.utime(self.a, (1000.0, 1000.0))
        self.assertEqual(run_lock.acquire(self.lock, self.b, now=1100.0)["status"], "busy")

    def test_force_release_and_the_command_line(self):
        run_lock.acquire(self.lock, self.a)
        self.assertEqual(run_lock.release(self.lock, force=True)["status"], "released")
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(run_lock.main(["--lock", self.lock, "acquire", "--run-dir", self.a]), 0)
            self.assertEqual(run_lock.main(["--lock", self.lock, "acquire", "--run-dir", self.b]), 1)
            self.assertEqual(run_lock.main(["--lock", self.lock, "status"]), 0)
        self.assertIn('"status": "busy"', out.getvalue())


class GpuStatusTest(unittest.TestCase):
    """scripts/gpu_status.py on fake KFD and DRM trees whose node order differs from the PCI order."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.kfd, self.drm = Path(tmp.name) / "kfd", Path(tmp.name) / "drm"
        nodes = self.kfd / "topology" / "nodes"
        # Two CPU nodes, then the GPUs in node order: HIP 0 is node 2, whose PCI bus (0x75) comes last
        for node, gpu_id, bus, minor in [(0, 0, 0, 0), (1, 0, 0, 0), (2, 111, 0x75, 128), (3, 222, 0x05, 136),
                                         (4, 333, 0x65, 144)]:
            (nodes / str(node)).mkdir(parents=True)
            (nodes / str(node) / "gpu_id").write_text("%d\n" % gpu_id)
            (nodes / str(node) / "properties").write_text("location_id %d\ndomain 0\ngfx_target_version %d\n"
                                                          "drm_render_minor %d\n" % (bus << 8, 90500 if gpu_id else 0, minor))
        self.use(128, 0.1, 0)
        self.use(136, 88, 100)
        self.use(144, 0.1, 0)
        for pid, gpu_id, size in [(4242, 222, 4096 * 2 ** 20), (5151, 333, 2 ** 19)]:  # 4 GiB on HIP 1, half a MiB on HIP 2
            (self.kfd / "proc" / str(pid)).mkdir(parents=True)
            (self.kfd / "proc" / str(pid) / ("vram_%d" % gpu_id)).write_text("%d\n" % size)

    def use(self, minor, vram_pct, busy, total=288 * 2 ** 30):
        dev = self.drm / ("renderD%d" % minor) / "device"
        dev.mkdir(parents=True, exist_ok=True)
        (dev / "mem_info_vram_total").write_text("%d\n" % total)
        (dev / "mem_info_vram_used").write_text("%d\n" % int(total * vram_pct / 100))
        (dev / "gpu_busy_percent").write_text("%d\n" % busy)

    def run_main(self, *args, env=None):
        out = io.StringIO()
        with redirect_stdout(out):
            rc = gpu_status.main(["--kfd", str(self.kfd), "--drm", str(self.drm), "--samples", "1", *args],
                                 env={} if env is None else env)
        return rc, json.loads(out.getvalue())

    def test_hip_numbers_follow_the_kfd_node_order_not_the_pci_address(self):
        rc, out = self.run_main()
        self.assertEqual(rc, 0)
        self.assertEqual([(r["hip"], r["node"], r["pci"], r["arch"]) for r in out["gpus"]],
                         [(0, 2, "0000:75:00.0", "gfx950"), (1, 3, "0000:05:00.0", "gfx950"), (2, 4, "0000:65:00.0", "gfx950")])
        self.assertEqual([(r["vram_pct"], r["busy_pct"], r["in_use"]) for r in out["gpus"]],
                         [(0.1, 0, False), (88.0, 100, True), (0.1, 0, False)])
        self.assertEqual([r["pids"] for r in out["gpus"]], [{}, {"4242": 4096}, {"5151": 1}],
                         "half a MiB shows as 1: the pids name holders and decide nothing")

    def test_a_gpu_is_in_use_from_5_percent_of_its_memory_or_of_its_busy_time(self):
        rc, out = self.run_main("--check", "all")
        self.assertEqual((rc, [r["hip"] for r in out["busy"]]), (1, [1]))
        self.assertEqual(self.run_main("--check", "0,2")[0], 0)
        for vram, busy, rc in [(4.9, 4, 0), (0.1, 5, 1), (5, 0, 1)]:
            self.use(128, vram, busy)
            self.assertEqual(self.run_main("--check", "0")[0], rc, "VRAM %s%%, GPU %s%%" % (vram, busy))
        rc, out = self.run_main("--check", "7")
        self.assertEqual((rc, out["missing"]), (1, [7]))

    def test_a_gpu_whose_use_cannot_be_read_is_not_idle(self):
        (self.drm / "renderD144" / "device" / "gpu_busy_percent").unlink()
        rc, out = self.run_main("--check", "2")
        self.assertEqual((rc, out["unknown"]), (1, [2]))

    def test_what_it_cannot_tell_stops_it(self):
        for args, env, says in [(["--check", "0-3"], {}, "comma-separated"),
                                (["--check", "1 0"], {}, "comma-separated"),  # not "10", nor GPU 1 alone
                                (["--check", ""], {}, "comma-separated"),
                                (["--check", " "], {}, "comma-separated"),
                                (["--check", "0,,2"], {}, "comma-separated"),
                                (["--check", "all"], {"ROCR_VISIBLE_DEVICES": "1,2"}, "ROCR_VISIBLE_DEVICES is set ('1,2')"),
                                (["--check", "all"], {"ROCR_VISIBLE_DEVICES": ""}, "ROCR_VISIBLE_DEVICES is set ('')")]:
            rc, out = self.run_main(*args, env=env)
            self.assertEqual(rc, 2, args)
            self.assertIn(says, out["error"])
        self.assertEqual(self.run_main("--check", " 0, 2 ")[0], 0, "spaces around a number are fine")
        (self.kfd / "topology" / "nodes" / "3" / "gpu_id").write_text("")  # dropped, HIP 2 would become HIP 1
        rc, out = self.run_main("--check", "all")
        self.assertEqual(rc, 2)
        self.assertIn("node 3", out["error"])
        shutil.rmtree(self.kfd / "topology")
        rc, out = self.run_main("--check", "all")
        self.assertEqual(rc, 2)
        self.assertIn("no GPU", out["error"])


class BaselineCacheTest(unittest.TestCase):
    """scripts/baseline_cache.py: what a fingerprint covers, what a store keeps and what a hit restores."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.model = self.root / "model"
        self.model.mkdir()
        (self.model / "config.json").write_text('{"num_hidden_layers": 36}')
        (self.model / "model.safetensors").write_bytes(b"\0" * 64)
        self.server = self.root / "server.sh"
        self.server.write_text("python -m sglang.launch_server --tp 1\n")
        self.client = self.root / "client.sh"
        self.client.write_text("python -m sglang.bench_serving --num-prompts 64\n")
        self.harness = self.root / "eval_accuracy.sh"
        self.harness.write_text("evalscope eval --datasets gsm8k\n")
        self.accuracy = self.root / "accuracy.sh"
        self.accuracy.write_text('ENABLE_THINKING="${ENABLE_THINKING:-1}"\n')
        self.evalscope = self.install_evalscope("1.2.0")
        self.env = {"INFERPILOT_IMAGE": "rocm/sgl-dev:v0.5.23-rocm10-mi35x-20261118", "JUDGE_MODEL": "Claude-Opus-5"}
        self.run_dir = self.root / "run"
        self.write_baseline(self.run_dir, ["gsm8k"], client=True)
        self.cache = str(self.root / "cache")

    def install_evalscope(self, version):
        """An evalscope in a venv of its own, as the bootstrap installs it."""
        venv = self.root / f"evalscope-{version}"
        (venv / "bin").mkdir(parents=True)
        (venv / "lib" / "python3.10" / "site-packages" / f"evalscope-{version}.dist-info").mkdir(parents=True)
        (venv / "bin" / "evalscope").write_text("#!/bin/sh\n")
        return str(venv / "bin" / "evalscope")

    def write_baseline(self, run_dir, sets, client, score=0.91):
        (run_dir / "accuracy").mkdir(parents=True, exist_ok=True)
        (run_dir / "accuracy" / "summary.txt").write_text("".join(
            f"baseline {s}: accept_length=[None] aggregate={{'score': {score}}} dir=baseline_{s}/x\n" for s in sets))
        summary = {"accuracy": {s: score for s in sets}, "times": {"launch_s": 400, "gsm8k_s": 500}}
        if client:
            (run_dir / "bench" / "baseline" / "1").mkdir(parents=True, exist_ok=True)
            (run_dir / "bench" / "baseline" / "1" / "result.json").write_text('{"output_throughput": 1000}')
            summary["perf"] = {"runs": [1000, 1010], "spread": "1%"}
        (run_dir / "baseline.json").write_text(json.dumps(summary))

    def key(self, env=None, **over):
        a = {"commit": "a" * 40, "model": str(self.model), "server_script": str(self.server),
             "client_script": str(self.client), "accuracy_script": str(self.accuracy), "workload": "vllm-49406 c1",
             "sets": "gsm8k", "harness": str(self.harness),
             "evalscope": self.evalscope, "gpu": "gfx950 MI355X", "image": self.env["INFERPILOT_IMAGE"], "extra": "gpus=1",
             **over}
        return baseline_cache.key_of(argparse.Namespace(**a), self.env if env is None else env)

    def store(self, key=None, run_dir=None, now=1000.0, env=None):
        return baseline_cache.store(self.cache, key or self.key(), str(run_dir or self.run_dir), now=now,
                                    env=self.env if env is None else env)

    def hit(self, key=None, now=2000.0):
        return baseline_cache.lookup(self.cache, key or self.key(), 7, now=now)["hit"]

    def published(self):
        return list(Path(self.cache).rglob("meta.json")) if Path(self.cache).exists() else []

    def test_a_hit_restores_accuracy_and_performance_at_their_paths(self):
        self.assertTrue(self.store()["stored"])
        again = self.root / "run2"
        res = baseline_cache.lookup(self.cache, self.key(), 7, restore_to=str(again), now=2000.0)
        self.assertTrue(res["hit"])
        self.assertEqual(res["run_dir"], str(self.run_dir))
        self.assertIn("0.91", (again / "accuracy" / "summary.txt").read_text())
        self.assertTrue((again / "bench" / "baseline" / "1" / "result.json").exists(), "bench/baseline keeps its path")
        self.assertEqual(json.loads((again / "baseline.json").read_text())["times"]["launch_s"], 400)

    def test_the_fingerprint_covers_what_the_numbers_depend_on(self):
        self.store()
        self.assertTrue(self.hit())
        for what, key in [
            ("another GPU of the same architecture", self.key(gpu="gfx950 MI350X")),
            ("more accuracy sets", self.key(sets="gsm8k gpqa_diamond simple_qa")),
            ("another image", self.key(image="rocm/sgl-dev:v0.5.23-rocm10-mi35x-20261120")),
            ("another judge", self.key(env={**self.env, "JUDGE_MODEL": "gpt-5"})),
            ("thinking off", self.key(env={**self.env, "ENABLE_THINKING": "0"})),
            ("another evalscope", self.key(evalscope=self.install_evalscope("1.3.0"))),
            ("another commit", self.key(commit="c" * 40)),
        ]:
            self.assertFalse(self.hit(key), what)
        self.assertEqual(self.key(sets="gsm8k gpqa_diamond simple_qa"), self.key(sets="simple_qa gsm8k gpqa_diamond"),
                         "the order of the sets does not matter")
        for path, edit in [(self.model / "config.json", '{"num_hidden_layers": 48}'),  # another checkpoint, same path
                           (self.server, "python -m sglang.launch_server --tp 2\n"),
                           (self.accuracy, 'ENABLE_THINKING="${ENABLE_THINKING:-0}"\n'),  # the rendered settings
                           (self.harness, "sgl-eval gsm8k\n")]:
            before = path.read_text()
            path.write_text(edit)
            self.assertFalse(self.hit(), f"{path.name} edited")
            path.write_text(before)
            self.assertTrue(self.hit(), f"{path.name} as it was")

    def test_nothing_is_cached_without_a_known_image_or_evalscope_version(self):
        for key, why in [(self.key(image=""), "the running image is unknown"),
                         (self.key(evalscope=str(self.root / "no-evalscope")), "the evalscope version is unknown")]:
            self.assertEqual(self.store(key)["reason"], why)
            self.assertEqual(baseline_cache.lookup(self.cache, key, 7)["reason"], why)
        self.assertEqual(self.published(), [])
        self.assertTrue(self.store(self.key(sets="", evalscope="", accuracy_script=""))["stored"],
                        "performance alone needs no evalscope and no accuracy script")

    def test_an_incomplete_or_failed_baseline_is_never_stored(self):
        self.assertEqual(self.store(self.key(sets="gsm8k simple_qa"))["reason"], "no score for simple_qa",
                         "a set the harness skipped (no judge) or that failed has no line in summary.txt")
        self.assertEqual(self.store(env={**self.env, "LIMIT": "5"})["reason"], "LIMIT is set: a smoke test, not a baseline")
        summary = json.loads((self.run_dir / "baseline.json").read_text())
        (self.run_dir / "baseline.json").write_text(json.dumps({**summary, "perf": {"runs": [1000]}}))
        self.assertEqual(self.store()["reason"], "baseline.json has fewer than two client runs")
        (self.run_dir / "baseline.json").write_text(json.dumps({**summary, "times": {}}))
        self.assertEqual(self.store()["reason"], "baseline.json has no times")
        shutil.rmtree(self.run_dir / "bench")
        self.assertEqual(self.store()["reason"], f"{os.path.join('bench', 'baseline')} is missing")
        self.assertEqual(self.published(), [])

    def test_a_task_without_a_client_stores_its_accuracy_alone(self):
        run = self.root / "accuracy-only"
        self.write_baseline(run, ["gsm8k"], client=False)
        out = self.store(self.key(client_script=""), run_dir=run)
        self.assertTrue(out["stored"], out)
        self.assertEqual(out["files"], ["baseline.json", "accuracy"])

    def test_a_store_that_fails_halfway_leaves_nothing(self):
        with mock.patch.object(baseline_cache.shutil, "copytree", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.store()
        self.assertEqual([p for p in Path(self.cache).rglob("*") if p.is_file()], [])

    def test_two_stores_in_the_same_second_keep_their_own_files(self):
        first = self.store()
        self.write_baseline(self.run_dir, ["gsm8k"], client=True, score=0.92)
        second = self.store()
        self.assertNotEqual(first["dir"], second["dir"])
        self.assertIn("0.91", (Path(first["dir"]) / "accuracy" / "summary.txt").read_text())
        self.assertIn("0.92", (Path(second["dir"]) / "accuracy" / "summary.txt").read_text())
        self.assertFalse(list(Path(self.cache).rglob("meta.json.part")))

    def test_an_entry_measured_under_an_older_rule_is_measured_again(self):
        older = {k: v for k, v in self.key().items() if k != "policy"}  # stored before every GPU had to be idle
        self.assertTrue(self.store(older)["stored"])
        self.assertTrue(baseline_cache.lookup(self.cache, older, 7, now=2000.0)["hit"])
        self.assertFalse(self.hit(), "the current rule misses it")

    def test_an_old_entry_is_not_used_and_goes_at_the_next_store(self):
        old = self.store(now=1000.0)
        self.assertFalse(self.hit(now=1000.0 + 8 * 86400))
        self.store(now=1000.0 + 8 * 86400)
        self.assertFalse(Path(old["dir"]).exists())

    def test_the_command_line(self):
        common = ["--cache", self.cache, "--commit", "b" * 40, "--model", str(self.model), "--server-script",
                  str(self.server), "--client-script", str(self.client), "--sets", "gsm8k", "--gpu", "gfx950 MI355X",
                  "--evalscope", self.evalscope]
        with redirect_stdout(io.StringIO()):
            self.assertEqual(baseline_cache.main(["lookup", *common], env=self.env), 1)
            self.assertEqual(baseline_cache.main(["store", *common, "--run-dir", str(self.run_dir)], env=self.env), 0)
            self.assertEqual(baseline_cache.main(["lookup", *common, "--restore-to", str(self.root / "run3")], env=self.env), 0)
        self.assertTrue((self.root / "run3" / "baseline.json").exists())
        err = io.StringIO()
        with redirect_stderr(err), self.assertRaises(SystemExit):
            baseline_cache.main(["lookup", *["origin/main" if x == "b" * 40 else x for x in common]], env=self.env)
        self.assertIn("full SHA", err.getvalue())


class LeakScanTest(unittest.TestCase):
    """scripts/leak_scan.py on a throwaway repository."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.repo = Path(tmp.name)
        git("init", "-q", "-b", "main", cwd=self.repo)
        (self.repo / "a.py").write_text("x = 1\n")
        git("add", "-A", cwd=self.repo)
        git("commit", "-q", "-m", "base", cwd=self.repo)
        self.base = subprocess.run(["git", "-C", str(self.repo), "rev-parse", "HEAD"], capture_output=True,
                                   text=True, check=True).stdout.strip()

    def commit(self, text, message="change", author="Jane Doe <jane@users.noreply.github.com>"):
        (self.repo / "a.py").write_text("x = 1\n" + text)
        name, email = author[:-1].split(" <")
        subprocess.run(["git", "-C", str(self.repo), "-c", "user.name=%s" % name, "-c", "user.email=%s" % email,
                        "commit", "-q", "-am", message], check=True, capture_output=True)

    def scan(self, *extra):
        out = io.StringIO()
        with redirect_stdout(out):
            rc = leak_scan.main(["--repo", str(self.repo), "--base", self.base, "--host", "gpu-node-17", *extra])
        return rc, json.loads(out.getvalue())

    def test_a_clean_branch_passes_and_rocm_paths_are_public(self):
        self.commit('LIB = "/opt/rocm/lib"\n')
        self.assertEqual(self.scan(), (0, {"leaks": [], "identities": []}))

    def test_local_paths_host_names_and_root_are_found(self):
        self.commit('MODEL = "/data/models/Qwen3-8B"\n', message="measured on gpu-node-17", author="root <root@gpu-node-17>")
        body = self.repo / "pr_body.md"
        body.write_text("Results\n\nlogs in /home/alice/run\n")
        rc, out = self.scan("--body", str(body))
        self.assertEqual(rc, 1)
        head = subprocess.run(["git", "-C", str(self.repo), "rev-parse", "HEAD"], capture_output=True, text=True).stdout
        self.assertEqual([f["where"] for f in out["leaks"]][0], "%s a.py:2" % head[:10])
        self.assertTrue(any(f["where"].startswith("commit ") for f in out["leaks"]), out)
        self.assertIn("%s:3" % body, [f["where"] for f in out["leaks"]])
        self.assertEqual(out["identities"], ["root <root@gpu-node-17>"])

    def test_a_path_upstream_carries_and_one_under_tmp_or_opt_are_public(self):
        (self.repo / "ci.py").write_text('os.environ.setdefault("HF_HOME", "/data2/models/huggingface")\n')
        git("add", "-A", cwd=self.repo)
        git("commit", "-q", "-m", "AMD CI's model location", cwd=self.repo)
        self.base = subprocess.run(["git", "-C", str(self.repo), "rev-parse", "HEAD"], capture_output=True,
                                   text=True, check=True).stdout.strip()
        self.commit('HF_HOME = "/data2/models/huggingface"\nOUT = "/tmp/sglang_test"\nPY = "/opt/conda/bin/python"\n')
        self.assertEqual(self.scan(), (0, {"leaks": [], "identities": []}))
        self.commit('MODEL = "/data2/models/Qwen3-8B"\nLOG = "/tmp/gpu-node-17/run.log"\n')
        rc, out = self.scan()
        self.assertEqual((rc, [f["text"] for f in out["leaks"]]),
                         (1, ['MODEL = "/data2/models/Qwen3-8B"', 'LOG = "/tmp/gpu-node-17/run.log"']),
                         "a path new to the repository, and the host name even under /tmp")
        self.assertEqual(self.scan("--allow", "/data2/models/Qwen3-8B", "--allow", "gpu-node-17")[0], 0)

    def test_an_internal_site_in_a_url_and_every_entry_of_a_path_list_are_found(self):
        self.commit('BUG = "https://jira.example/browse/ABC-1"\nENV = "PYTHONPATH=/opt/a:/home/alice/x"\n'
                    'LIBS = "/opt/rocm/lib:/home/alice/aiter/build"\nPR = "https://github.com/sgl-project/sglang/pull/1"\n',
                    message="as in https://confluence.example/pages/123")
        rc, out = self.scan()
        self.assertEqual((rc, [f["text"] for f in out["leaks"]]),
                         (1, ['BUG = "https://jira.example/browse/ABC-1"', 'ENV = "PYTHONPATH=/opt/a:/home/alice/x"',
                              'LIBS = "/opt/rocm/lib:/home/alice/aiter/build"', "as in https://confluence.example/pages/123"]),
                         "a public URL is no finding")

    def test_the_company_network_is_found_and_public_amd_sites_are_not(self):
        # In pieces: tests/test_hygiene.py keeps each of these out of this repository's own files.
        lines = ["https://ci" + ".internal/build/42", "https://acme." + "atlassian.net/browse/ABC-1",
                 "https://corp." + "sharepoint.com/sites/private", "https://wiki." + "amd.com/x",
                 "curl http://10." + "1.2.3:8000/v1/models", "jane@ci" + ".internal"]
        self.commit("".join("# %s\n" % line for line in lines + ["https://rocm.docs.amd.com/en/latest/"]))
        rc, out = self.scan()
        self.assertEqual((rc, [f["text"] for f in out["leaks"]]), (1, ["# %s" % line for line in lines]))

    def test_a_path_after_an_option_or_in_a_shell_default_is_found(self):
        self.commit('CFLAGS = "-I/home/alice/include"\nMODEL = "${MODEL_DIR:-/data/models/m}"\nLOG = "/data/opt/rocm"\n'
                    'LIBS = "-L/opt/rocm/lib"\nREL = "sgl-I/home/alice"\n')
        rc, out = self.scan()
        self.assertEqual((rc, [f["text"] for f in out["leaks"]]),
                         (1, ['CFLAGS = "-I/home/alice/include"', 'MODEL = "${MODEL_DIR:-/data/models/m}"', 'LOG = "/data/opt/rocm"']),
                         "/opt is public wherever it is, and a relative path is no path")

    def test_root_in_a_commit_message_is_found(self):
        self.commit("y = 2\n", message="change\n\nCo-Authored-By: Claude <noreply@anthropic.com>\n"
                                       "Signed-off-by: root <root@a1b2c3d4e5f6>")
        rc, out = self.scan()
        self.assertEqual((rc, [f["text"] for f in out["leaks"]]), (1, ["Signed-off-by: root <root@a1b2c3d4e5f6>"]))

    def test_a_file_git_does_not_diff_and_a_byte_that_is_not_utf8_are_read(self):
        (self.repo / ".gitattributes").write_text("*.cfg -diff\n")
        (self.repo / "run.cfg").write_text("model=/home/alice/secret\n")
        (self.repo / "b.py").write_bytes(b"# caf\xe9\nM = '/home/alice/m'\n")
        git("add", "-A", cwd=self.repo)
        self.commit("")
        rc, out = self.scan()
        self.assertEqual((rc, sorted(f["text"] for f in out["leaks"])), (1, ["M = '/home/alice/m'", "model=/home/alice/secret"]))

    def test_the_name_of_an_added_or_renamed_file_is_found(self):
        (self.repo / "logs").mkdir()
        (self.repo / "logs" / "gpu-node-17.txt").write_text("")
        (self.repo / "trace.json").write_text("{}\n")
        git("add", "-A", cwd=self.repo)
        self.commit("", message="an empty log")
        git("mv", "trace.json", "trace_gpu-node-17_1.json", cwd=self.repo)
        self.commit("", message="the trace renamed")
        rc, out = self.scan()
        self.assertEqual((rc, [f["text"] for f in out["leaks"]]), (1, ["logs/gpu-node-17.txt", "trace_gpu-node-17_1.json"]))

    def test_an_identity_with_the_host_name_or_an_internal_site_is_found(self):
        site = "jane@ci" + ".internal"
        self.commit("y = 2\n", author="gpu-node-17 <jane@users.noreply.github.com>")
        self.commit("y = 3\n", author="Jane <%s>" % site)
        rc, out = self.scan()
        self.assertEqual((rc, out["identities"]), (1, ["Jane <%s>" % site, "gpu-node-17 <jane@users.noreply.github.com>"]))

    def test_an_internal_host_by_name_is_found_and_a_module_path_is_not(self):
        lines = ["HOST=ci" + ".internal", "ssh ci" + ".internal"]
        self.commit("".join("%s\n" % line for line in lines
                            + ["from google.protobuf.internal import builder", "t = list(self.unique_id.internal)",
                               "code = grpc.StatusCode.INTERNAL"]))
        rc, out = self.scan()
        self.assertEqual((rc, [f["text"] for f in out["leaks"]]), (1, lines))

    def test_the_network_inside_a_public_path_is_found(self):
        lines = ["LOG = '/tmp/10." + "1.2.3/run.log'", "CACHE = '/opt/wiki." + "amd.com/cache'"]
        self.commit("".join("%s\n" % line for line in lines))
        rc, out = self.scan()
        self.assertEqual((rc, [f["text"] for f in out["leaks"]]), (1, lines))

    def test_a_carriage_return_or_a_vertical_tab_inside_a_line_hides_nothing(self):
        (self.repo / "log.txt").write_bytes(b"progress\rmodel=/home/alice/private\na\x0bLOG=/home/alice/log\n")
        git("add", "-A", cwd=self.repo)
        self.commit("")
        rc, out = self.scan()
        self.assertEqual((rc, len(out["leaks"])), (1, 2))

    def test_a_sibling_in_the_cluster_is_found(self):
        self.commit("ssh gpu-node-18\nARGS = '--dist-init-addr gpu-node-18:5000'\nGPUS = 'gpu-nodes'\n")
        rc, out = self.scan()
        self.assertEqual((rc, [f["text"] for f in out["leaks"]]),
                         (1, ["ssh gpu-node-18", "ARGS = '--dist-init-addr gpu-node-18:5000'"]),
                         "a word that only starts like the cluster's names is none")

    def test_a_shell_default_without_a_colon_and_a_file_uri_are_read_as_paths(self):
        self.commit("MODEL=${MODEL_DIR-/data/models/m}\nTMP = 'file:///tmp/sglang/x'\nLIB = 'file:///opt/rocm/lib'\n"
                    "HOME = 'file:///home/alice/x'\n")
        rc, out = self.scan()
        self.assertEqual((rc, [f["text"] for f in out["leaks"]]),
                         (1, ["MODEL=${MODEL_DIR-/data/models/m}", "HOME = 'file:///home/alice/x'"]),
                         "/tmp and /opt stay public in a file URI")

    def test_more_storage_roots_are_local(self):
        lines = ["/scratch/alice/x", "/raid/alice/models", "--model-path /models/DeepSeek-V4.1-Flash", "/nfs_home/alice/x",
                 "/export/alice/x", "/work/alice/x", "/kmd3/alice/x"]
        self.commit("".join("%s\n" % line for line in lines))
        rc, out = self.scan()
        self.assertEqual((rc, [f["text"] for f in out["leaks"]]), (1, lines))

    def test_the_default_host_is_the_name_without_its_domain(self):
        self.commit("ssh gpu-node-17\n")
        out = io.StringIO()
        with mock.patch("socket.gethostname", return_value="gpu-node-17.cluster.example"), redirect_stdout(out):
            rc = leak_scan.main(["--repo", str(self.repo), "--base", self.base])
        self.assertEqual((rc, [f["text"] for f in json.loads(out.getvalue())["leaks"]]), (1, ["ssh gpu-node-17"]))

    def test_an_added_line_that_reads_like_a_file_header_is_content(self):
        self.commit("++ /home/alice/private\n")
        rc, out = self.scan()
        self.assertEqual((rc, [f["text"] for f in out["leaks"]]), (1, ["++ /home/alice/private"]))

    def test_an_external_diff_tool_cannot_hide_a_line(self):
        self.commit('LOG = "/home/alice/run.log"\n')
        subprocess.run(["git", "-C", str(self.repo), "config", "diff.external", "true"], check=True)
        self.assertEqual(self.scan()[0], 1)

    def test_a_line_a_later_commit_removed_is_still_found(self):
        self.commit('LOG = "/home/alice/run.log"\n', message="first try")
        first = subprocess.run(["git", "-C", str(self.repo), "rev-parse", "HEAD"], capture_output=True, text=True).stdout
        self.commit("", message="drop the log path")
        rc, out = self.scan()
        self.assertEqual(rc, 1)
        self.assertEqual([f["where"] for f in out["leaks"]], ["%s a.py:2" % first[:10]])


class ScanCiTest(unittest.TestCase):
    ENTRIES = {
        "test/a.py::A.setUpClass": {"cluster": "ImportError: _check removed", "status": "recurring (new regression since 2026-10-21)",
                                    "error": "ImportError: cannot import name '_check'", "first_seen": "2026-10-22", "last_seen": "2026-10-23",
                                    "job_url": "https://ci/1"},
        "test/b.py::B.test_x": {"cluster": "ImportError: _check removed", "status": "never-passed (rocm720 lane, since 2026-10-20)",
                                "error": "ImportError", "first_seen": "2026-10-22", "last_seen": "2026-10-25"},
        "test/c.py::C.test_y": {"cluster": "runner lost communication", "status": "flaky", "first_seen": "2026-10-24", "last_seen": "2026-10-24"},
    }

    def body(self, state):
        return "## Tracker\nsome table\n<!-- ci-failure-tracker-state: " + json.dumps(state) + " -->\ntrailing text"

    def test_parse_state_direct_and_wrapped(self):
        self.assertEqual(scan_ci.parse_state(self.body(self.ENTRIES)), self.ENTRIES)
        self.assertEqual(scan_ci.parse_state(self.body({"entries": self.ENTRIES})), self.ENTRIES)
        self.assertEqual(scan_ci.parse_state("no state here"), {})
        self.assertEqual(scan_ci.parse_state("<!-- ci-failure-tracker-state: {broken -->"), {})

    def test_aggregate(self):
        clusters = scan_ci.aggregate(self.ENTRIES)
        top = clusters[0]
        self.assertEqual(top["cluster"], "ImportError: _check removed")
        self.assertEqual(top["n_tests"], 2)
        self.assertEqual(top["since"], "2026-10-20")
        self.assertEqual((top["first_seen"], top["last_seen"]), ("2026-10-22", "2026-10-25"))
        self.assertEqual(top["statuses"], {"recurring": 1, "never-passed": 1})
        self.assertEqual(clusters[1]["statuses"], {"flaky": 1})

    # The state as the bot writes it since version 3: counts in place of a status, the runs that failed, the
    # fix PR its analysis names, and the tests that stopped failing in an archive of bare rows.
    V3 = {
        "version": 3,
        "active": {
            "test/a.py::A.test_x": {"cluster": "enterContext on Python 3.10", "error": "AttributeError: enterContext",
                                    "job_url": "https://ci/1", "first_seen": "2026-10-21", "last_seen": "2026-11-23",
                                    "fix_pr": "[#49963](https://github.com/o/r/pull/49963)", "fail_runs": ["11", "12", "14"],
                                    "variants": ["rocm720", "rocm724"], "failed": 3, "observed": 5, "streak": 2},
            "test/b.py::B.test_y": {"cluster": "enterContext on Python 3.10", "first_seen": "2026-11-20", "last_seen": "2026-11-23",
                                    "fix_pr": "", "fail_runs": ["12", "14"], "variants": ["rocm720"], "failed": 2, "observed": 2,
                                    "streak": 2},
            "test/c.py::C.test_z": {"cluster": "runner lost communication", "first_seen": "2026-11-01", "last_seen": "2026-11-02",
                                    "fix_pr": "", "fail_runs": ["9"], "variants": ["rocm10"], "failed": 1, "observed": 7, "streak": 0},
        },
        "archive": [["test/d.py::D.test_w", "2026-10-23", "2026-10-24", 1, 14, "timed out"]],
        "runs": {"9": ["2026-11-01T01:00:00Z", "rocm10"], "11": ["2026-11-10T01:00:00Z", "rocm720"],
                 "12": ["2026-11-20T01:00:00Z", "rocm720"], "13": ["2026-11-21T01:00:00Z", "rocm720"],
                 "14": ["2026-11-23T01:00:00Z", "rocm720"]},
    }

    def test_the_version_3_state_is_read_as_its_active_entries(self):
        body = self.body(self.V3)
        self.assertEqual(scan_ci.state_form(scan_ci.read_state(body)), "counts")
        entries = scan_ci.parse_state(body)
        self.assertEqual(sorted(entries), sorted(self.V3["active"]),
                         "the state's own keys (version, active, archive, runs) are not tests")
        a, b, c = (entries[k] for k in sorted(entries))
        self.assertEqual(a["status"], "failing now (2 in a row, since 2026-11-20; failed 3 of 5 runs)")
        self.assertEqual(b["status"], "failing every run (2 of 2 runs, since 2026-11-20)")
        self.assertEqual(c["status"], "not failing now (failed 1 of 7 runs)")
        self.assertEqual([e["failing_since"] for e in (a, b, c)], ["2026-11-20", "2026-11-20", ""],
                         "a streak begins at its first run of the failures in a row, by when the runs started")
        self.assertEqual([e["fix_prs"] for e in (a, b, c)], [[49963], [], []])
        self.assertEqual(self.V3["active"]["test/a.py::A.test_x"].get("status"), None, "the state it was given is left as it was")

    def test_a_streak_whose_runs_the_state_no_longer_dates(self):
        state = json.loads(json.dumps(self.V3))
        state["runs"] = {"14": ["2026-11-23T01:00:00Z", "rocm720"]}
        entries = scan_ci.parse_state(self.body(state))
        self.assertEqual(entries["test/b.py::B.test_y"]["failing_since"], "2026-11-20",
                         "it failed every run it had: the streak began when it was first seen")
        self.assertEqual(entries["test/a.py::A.test_x"]["failing_since"], "", "it passed in between: no date is made up")
        self.assertEqual(entries["test/a.py::A.test_x"]["status"], "failing now (2 in a row; failed 3 of 5 runs)")

    def test_the_version_3_clusters_carry_the_variants_and_the_fix_prs(self):
        clusters = scan_ci.aggregate(scan_ci.parse_state(self.body(self.V3)))
        top = clusters[0]
        self.assertEqual((top["cluster"], top["n_tests"]), ("enterContext on Python 3.10", 2))
        self.assertEqual(top["statuses"], {"failing now": 1, "failing every run": 1})
        self.assertEqual(top["since"], "2026-11-20")
        self.assertEqual((top["variants"], top["fix_prs"]), (["rocm720", "rocm724"], [49963]))
        self.assertEqual(clusters[1]["statuses"], {"not failing now": 1})
        self.assertEqual((clusters[1]["variants"], clusters[1]["fix_prs"]), (["rocm10"], []))
        old = scan_ci.aggregate(self.ENTRIES)[0]
        self.assertEqual((old["variants"], old["fix_prs"]), ([], []), "the earlier form names neither")

    def test_a_state_in_another_form_is_not_read_as_entries(self):
        for state in ({"version": 4, "tests": [{"name": "t"}]}, {"version": 3, "active": ["not", "a", "map"]}, ["a", "list"]):
            body = self.body(state)
            self.assertEqual(scan_ci.state_form(scan_ci.read_state(body)), "unknown", state)
            self.assertEqual(scan_ci.parse_state(body), {}, "its top-level keys were once taken for tests: %r" % (state,))
        self.assertEqual(scan_ci.state_form(scan_ci.read_state("no state here")), "none")
        self.assertEqual(scan_ci.state_form(scan_ci.read_state(self.body({}))), "status", "an empty state has no entry, and is read")

    def test_main_says_which_form_it_read_and_what_it_could_not_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = {}
            for name, state in (("v3", self.V3), ("old", self.ENTRIES), ("other", {"version": 4, "tests": []})):
                body, res, md = (Path(tmp) / (name + ext) for ext in (".md", ".json", ".out.md"))
                body.write_text(self.body(state))
                with mock.patch.object(sys, "argv", ["scan_ci.py", "--file", str(body), "--out", str(res), "--md", str(md)]), \
                        redirect_stderr(io.StringIO()):
                    self.assertEqual(scan_ci.main(), 0)
                out[name] = (json.loads(res.read_text())["trackers"][0], md.read_text())
        v3, old, other = (out[k][0] for k in ("v3", "old", "other"))
        self.assertEqual((v3["state_form"], v3["n_entries"], v3["n_archived"]), ("counts", 3, 1))
        self.assertEqual((old["state_form"], old["n_entries"]), ("status", 3))
        self.assertNotIn("n_archived", old)
        self.assertEqual((other["state_form"], other["n_entries"], other["clusters"]), ("unknown", 0, []))
        self.assertIn("does not read (version 4", other["unread"])
        self.assertIn("**Not read:**", out["other"][1], "the report says so where the clusters would be")
        self.assertIn("| #49963 |", out["v3"][1], "the report names the fix PR of a cluster")

    LOG = textwrap.dedent("""\
        2026-11-09T02:56:19.1234567Z \x1b[36;1m[server-test] FAILED during server startup: wan_lora\x1b[0m
        2026-11-09T03:40:01.0000000Z E   RuntimeError: Server exited early (code 1).
        2026-11-09T03:40:01.0000000Z E   RuntimeError: Server exited early (code 1).
        2026-11-09T03:40:02.0000000Z =========================== short test summary info ============================
        2026-11-09T03:40:02.0000000Z ERROR sglang/multimodal_gen/test/server/test_server_2_gpu.py::TestTwoGpu::test_gen[wan_lora]
        2026-11-09T03:40:02.0000000Z = 14 passed, 1 skipped, 1 error in 2566.37s (0:42:46) =
        2026-11-09T03:40:03.0000000Z ======================== 2 skipped, 3 warnings in 7.17s ========================
        2026-11-09T03:40:04.0000000Z Exception: Server process exited with code -9. Check server logs for errors.
        2026-11-09T03:40:04.0000000Z \u2717 FAILED: /sglang-checkout/test/registered/amd/accuracy/test_qwen_eval.py returned exit code 1
        2026-11-09T03:40:05.0000000Z ##[error]Process completed with exit code 1.
        """)

    def test_parse_log_reads_pytest_and_run_suite(self):
        got = scan_ci.parse_log(self.LOG)
        self.assertEqual(got["failed_tests"], ["sglang/multimodal_gen/test/server/test_server_2_gpu.py::TestTwoGpu::test_gen[wan_lora]",
                                               "test/registered/amd/accuracy/test_qwen_eval.py"])
        self.assertEqual(got["errors"], ["RuntimeError: Server exited early (code 1).",
                                         "Exception: Server process exited with code -9. Check server logs for errors."])
        self.assertEqual(got["totals"], "14 passed, 1 skipped, 1 error")
        self.assertEqual(got["gh_errors"], ["Process completed with exit code 1."])

    def test_main_status_finds_since_when_a_job_fails(self):
        def run(i, event="schedule", repo="sgl-project/sglang"):
            return {"id": i, "event": event, "name": "Nightly Test (AMD)", "conclusion": "failure", "created_at": "2026-11-0%dT21:04:00Z" % i,
                    "head_sha": "%040d" % i, "html_url": "https://github.com/r/%d" % i, "head_repository": {"full_name": repo}}

        def job(name, conclusion):
            return {"id": hash(name) % 1000, "name": name, "conclusion": conclusion, "html_url": "https://github.com/j",
                    "steps": [{"name": "Accuracy " + name, "conclusion": conclusion}]}

        runs = [run(8), run(7, "pull_request", "someone/fork"), run(6), run(5), run(4)]
        jobs = {8: [job("acc-qwen", "failure"), job("acc-glm", "success"), job("check-all-jobs", "failure"), job("pr-test-amd-finish", "failure")],
                6: [job("acc-qwen", "failure"), job("acc-glm", "success")],
                5: [job("acc-qwen", "success")], 4: [job("acc-qwen", "failure")]}

        def get(path, params=None):
            if path.endswith("/runs"):
                return {"workflow_runs": runs}
            return {"jobs": jobs[int(path.split("/")[-2])]}

        with mock.patch.object(scan_ci.gh, "get", side_effect=get), \
                mock.patch.object(scan_ci.gh, "text", return_value=self.LOG):
            got = scan_ci.main_status("sgl-project/sglang", ["nightly-test-amd.yml"])
        self.assertEqual(got["workflows"][0]["failed_jobs"], 1, "check-all-jobs and *-finish only report the others")
        f = got["failures"][0]
        self.assertEqual((f["job"], f["consecutive_failures"]), ("acc-qwen", 2))
        self.assertEqual((f["failing_since"]["run_at"], f["last_success"]["run_at"]), ("2026-11-06T21:04:00Z", "2026-11-05T21:04:00Z"))
        self.assertEqual(f["failed_steps"], ["Accuracy acc-qwen"])
        self.assertIn("test/registered/amd/accuracy/test_qwen_eval.py", f["failed_tests"])


class ScanIssuesTest(unittest.TestCase):
    def test_classify_kind(self):
        self.assertEqual(scan_issues.classify_kind("AMD Development Roadmap (2026 Q4)", []), "roadmap")
        self.assertEqual(scan_issues.classify_kind("[Failure Tracker] PR Test (AMD)", []), "tracker")
        self.assertEqual(scan_issues.classify_kind("anything", ["ci-failure-tracker"]), "tracker")
        self.assertEqual(scan_issues.classify_kind("[AITER-Upgrade] PR readiness", []), "internal")
        self.assertEqual(scan_issues.classify_kind("[Bug] GLM 5.3 segfault on MI300", []), "user")

    def test_cites_issue(self):
        self.assertTrue(scan_issues.cites_issue("Fixes #38849", 38849))
        self.assertTrue(scan_issues.cites_issue("see https://github.com/sgl-project/sglang/issues/38849.", 38849))
        self.assertTrue(scan_issues.cites_issue("related to issue 38849", 38849))
        self.assertFalse(scan_issues.cites_issue("bumps #388490 and port 38849", 38849))

    def test_closes_issue(self):
        for text in ("Fixes #49602. The AMD nightly workflow could report ...", "closes: #49602",
                     "Resolved sgl-project/sglang#49602", "Fix https://github.com/sgl-project/sglang/issues/49602."):
            self.assertTrue(scan_issues.closes_issue(text, 49602, "sgl-project/sglang"), text)
        for text in ("#41071 added the CK GEMM behind a switch; the caveat of #49602 depends on the settings",
                     "Fix HiCache TP rank divergence (#49602)", "a prefix fix, #49602 has the numbers",
                     "Fixes #496020", "Fixes vllm-project/vllm#49602", "a quick fix\n#49602 explains the numbers"):
            self.assertFalse(scan_issues.closes_issue(text, 49602, "sgl-project/sglang"), text)

    def test_a_linked_pr_that_only_cites_the_issue_does_not_close_it(self):
        found = [{"number": 43815, "title": "[Perf] [AMD] fp8: Use the AITER CK blockscale GEMM by default on gfx942",
                  "body": "#41071 added it off by default; the accuracy caveat of #43526 depends on the settings.",
                  "state": "closed", "pull_request": {"merged_at": "2026-10-27T08:00:00Z"}, "created_at": "2026-10-20T08:00:00Z"},
                 {"number": 52001, "title": "[Fix] [AMD] fp8: Keep gfx942 block-FP8 on the Triton GEMM", "body": "Fixes #43526.",
                  "state": "open", "pull_request": {"merged_at": None}, "created_at": "2026-11-20T08:00:00Z"},
                 {"number": 52002, "title": "[AMD] moe: dispatch logging fix", "body": "#43526 is background for this change.",
                  "state": "open", "pull_request": {"merged_at": None}, "created_at": "2026-11-21T08:00:00Z"},
                 {"number": 52003, "title": "Fix #43526: keep gfx942 block-FP8 on the Triton GEMM", "body": "Measured on MI300X.",
                  "state": "open", "pull_request": {"merged_at": None}, "created_at": "2026-11-22T08:00:00Z"},
                 {"number": 43526, "title": "the issue itself", "body": "", "state": "open"}]
        with mock.patch.object(scan_issues.gh, "search_issues", return_value=found):
            prs = scan_issues.linked_prs("sgl-project/sglang", 43526)
        self.assertEqual([(p["number"], p["state"], p["closes"]) for p in prs],
                         [(43815, "merged", False), (52001, "open", True), (52002, "open", False), (52003, "open", True)],
                         "a title ending in fix does not close with a body opening with the issue; a closing title does")

    def test_an_amd_gpu_named_only_in_the_body_is_collected(self):
        new = {"created_at": "2026-11-20T08:00:00Z", "updated_at": "2026-11-20T08:00:00Z"}
        by_title = {"number": 1, "title": "[Bug] ROCm: aiter MLA decode NaN", "body": "", **new}
        both = {"number": 2, "title": "[Bug] hang on MI300X", "body": "8x MI300X", **new}
        by_body = {"number": 3, "title": "[Bug] attention-DP ranks pick different graph variants",
                   "body": "Steps:\n1. launch\n\nHardware: 8xMI355X, TP8\n", **new}
        no_gpu = {"number": 4, "title": "[Bug] Tools and JSON schema cannot be combined",
                  "body": "ROCM used to build PyTorch: N/A\nHIP runtime version: N/A\nCPU: AMD EPYC 9654", **new}

        def search(query, **_):
            if "in:title" in query:
                return [by_title, both] if "ROCm" in query or "MI300X" in query else []
            return [both, by_body, no_gpu]

        with mock.patch.object(scan_issues.gh, "search_issues", side_effect=search) as s:
            items = scan_issues.collect("sgl-project/sglang", None, None)
        body_queries = [c.args[0] for c in s.call_args_list if "in:body" in c.args[0]]
        self.assertTrue(body_queries, "the body is searched too")
        for q in body_queries:
            self.assertIsNone(re.search(r"\b(AMD|ROCm|HIP)\b", q), "collect_env and the CPU say these in CUDA reports: " + q)
        self.assertEqual({it["number"]: it["found_in"] for it in items}, {1: "title", 2: "title", 3: "body"},
                         "a title match stays one; a body search hit is kept only when gpu_lines finds a GPU name")
        now = scan_issues.parse_time("2026-11-23T00:00:00Z")
        rows = {it["number"]: scan_issues.summarize(it, now) for it in items}
        self.assertEqual(rows[3]["gpu_lines"], ["Hardware: 8xMI355X, TP8"])
        self.assertNotIn("gpu_lines", rows[1])

        with mock.patch.object(scan_issues.gh, "get", return_value={"number": 5, "title": "t", **new}):
            self.assertEqual(scan_issues.collect("sgl-project/sglang", 5, None)[0]["found_in"], "issue")

    def test_gpu_names(self):
        for text in ("8xMI300X", "8x MI355X, TP8", "MI300A", "tested on MI35x", "MI30X nightly", "mi325x",
                     "libaiter_gfx942.so", "module_fmha_v3_fwd_gfx950_bf16", "tuned_MI300X.csv", "gfx942:sramecc+"):
            with self.subTest(text=text):
                self.assertEqual(scan_issues.gpu_lines(text), [text])
        for text in ("MI3000", "XIAOMI300", "MI250X", "gfx9500", "gfx1100", "agfx942", "CPU: AMD EPYC 9654",
                     "ROCM used to build PyTorch: N/A", "HIP runtime version: N/A"):
            with self.subTest(text=text):
                self.assertEqual(scan_issues.gpu_lines(text), [])

    def test_gpu_lines_show_the_name_and_stop_at_three(self):
        long = "A" * 300 + " the run on 8x MI355X hangs " + "B" * 300
        (cut,) = scan_issues.gpu_lines(long)
        self.assertIn("8x MI355X", cut, "a name past the first 200 characters is still in the line")
        self.assertLessEqual(len(cut), 206)
        body = "a MI300X\nb gfx942\na MI300X\nc MI355X\nd MI308X"
        self.assertEqual(scan_issues.gpu_lines(body), ["a MI300X", "b gfx942", "c MI355X"])

    def test_projected_auto_close(self):
        self.assertEqual(scan_issues.projected_auto_close("2026-09-09T10:00:00Z"), "2026-11-09")
        self.assertEqual(scan_issues.projected_auto_close("2026-09-09T00:00:00Z"), "2026-11-08")

    def test_reply_status(self):
        rep = "alice"
        self.assertTrue(scan_issues.reply_status([{"user": {"login": "alice"}}], rep)["only_reporter_or_bots"])
        bot = [{"user": {"login": "github-actions[bot]"}, "author_association": "CONTRIBUTOR"}]
        self.assertEqual(scan_issues.reply_status(bot, rep)["replies_from_others"], 0)
        maint = [{"user": {"login": "bob"}, "author_association": "COLLABORATOR"}]
        s = scan_issues.reply_status(maint, rep)
        self.assertEqual((s["replies_from_others"], s["maintainer_replied"]), (1, True))


GIT_LOG = (
    "\x1eaaaa1111\x1f2026-11-02T10:00:00+08:00\x1fdev\x1f[Perf] kda: Batch FlashInfer prefill copies (#47651)\n"
    "diff --git a/python/sglang/srt/layers/kda.py b/python/sglang/srt/layers/kda.py\n"
    "--- a/python/sglang/srt/layers/kda.py\n"
    "+++ b/python/sglang/srt/layers/kda.py\n"
    "@@ -10,0 +11,3 @@\n"
    "+from flashinfer import prefill\n"
    "+if _is_cuda and x.is_cuda:\n"
    "+    y = t.is_cuda\n"
    "\x1ebbbb2222\x1f2026-11-02T11:00:00+08:00\x1fdev\x1f[Fix] [AMD] aiter: guard import (#47700)\n"
    "diff --git a/python/sglang/srt/x.py b/python/sglang/srt/x.py\n"
    "--- a/python/sglang/srt/x.py\n"
    "+++ b/python/sglang/srt/x.py\n"
    "@@ -1,0 +2,1 @@\n"
    "+if _is_cuda:\n"
    "\x1ecccc3333\x1f2026-11-02T12:00:00+08:00\x1fdev\x1f[Feat] [XPU] graph: Add full prefill graph (#30980)\n"
    "diff --git a/python/sglang/srt/g.py b/python/sglang/srt/g.py\n"
    "--- a/python/sglang/srt/g.py\n"
    "+++ b/python/sglang/srt/g.py\n"
    "@@ -1,0 +2,1 @@\n"
    "+from flashinfer import x\n"
    "\x1edddd4444\x1f2026-11-02T13:00:00+08:00\x1fdev\x1f[Feat] diffusion/model: support HiDream-O1-Image (#39682)\n"
    "diff --git a/python/sglang/multimodal_gen/runtime/models/dits/hidream.py b/python/sglang/multimodal_gen/runtime/models/dits/hidream.py\n"
    "--- /dev/null\n"
    "+++ b/python/sglang/multimodal_gen/runtime/models/dits/hidream.py\n"
    "@@ -0,0 +1,1 @@\n"
    "+class HiDream: pass\n"
)


class ScanUpstreamTest(unittest.TestCase):
    def test_parse_and_candidates(self):
        commits = scan_upstream.parse_log(GIT_LOG)
        self.assertEqual(len(commits), 4)
        first = commits[0]
        self.assertEqual(first["pr"], 47651)
        self.assertEqual([g["line"] for g in first["gates"]], [12])  # `t.is_cuda` (line 13) is not a gate
        self.assertIn("flashinfer", first["cuda_deps"])
        cands = {c["pr"]: c["kinds"] for c in scan_upstream.candidates(commits)}
        self.assertEqual(cands[47651], ["new_cuda_gate", "cuda_first_feature"])
        self.assertNotIn(47700, cands)   # AMD work
        self.assertNotIn(30980, cands)   # another platform
        self.assertEqual(cands[39682], ["new_model"])

    def test_untagged_subject(self):
        self.assertTrue(scan_upstream.parse_subject("srt: Support FooModel (#1)")["is_feature"])
        self.assertFalse(scan_upstream.parse_subject("[Fix] srt: Add a guard (#2)")["is_feature"])


class AuditPlatformGatesTest(unittest.TestCase):
    def test_python_rules(self):
        src = textwrap.dedent("""\
            if _is_cuda:
                pass
            ok = t.is_cuda
            if not _is_hip:
                pass
            raise ValueError("CUDA only")
            default = "trtllm" if major >= 10 else "fa3"
            if is_gfx95_supported():
                pass
            """)
        cats = {e["line"]: e for e in audit_platform_gates.scan_text("x.py", src, audit_platform_gates.PY_RULES)}
        self.assertEqual(cats[1]["category"], "cuda_gate")
        self.assertNotIn(3, cats)
        self.assertEqual(cats[4]["category"], "hip_excluded")
        self.assertEqual(cats[6]["category"], "unsupported_note")
        self.assertEqual(cats[7]["category"], "capability_check")
        self.assertIn("gfx1250", cats[7]["note"])
        self.assertEqual(cats[8]["category"], "arch_gate")

    def test_device_rules(self):
        src = "v += __shfl_xor_sync(0xffffffffu, v, 16);\nconstexpr int kWarpSize = 32;\nok();\n"
        hip = audit_platform_gates.scan_text("jit/csrc/norm_rope.cuh", src, audit_platform_gates.DEVICE_RULES)
        self.assertEqual([e["line"] for e in hip], [1, 2])
        self.assertTrue(hip[0]["note"].startswith("built for HIP"))
        nv = audit_platform_gates.scan_text("csrc/cutlass_ext/x.cuh", src, audit_platform_gates.DEVICE_RULES)
        self.assertEqual(nv[0]["note"], "NVIDIA-only file?")


class AuditHostSyncsTest(unittest.TestCase):
    SRC = textwrap.dedent("""\
        import torch

        class Backend:
            def __init__(self, x):
                self.n = int(x.max().item())

            def init_forward_metadata(self, batch):
                total = int(batch.seq_lens.sum().item())
                host = batch.seq_lens_cpu.max().item()
                idx = batch.page_indptr.to(self.device)
                fast = batch.page_indptr.to(self.device, non_blocking=True)
                t = torch.tensor([1, 2], device=self.device)
                return total, host, idx, fast, t

            def init_cuda_graph_state(self, x):
                return x.tolist()

            def forward_extend(self, q):
                torch.cuda.synchronize()
                return q
        """)

    def test_findings(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "backend.py"
            p.write_text(self.SRC)
            found = audit_host_syncs.scan([p])
        by_line = {f["line"]: f for f in found}
        self.assertEqual(by_line[5]["temperature"], "cold")                 # __init__
        self.assertEqual((by_line[8]["kind"], by_line[8]["temperature"]), ("sync", "warm"))
        self.assertEqual(by_line[9]["kind"], "host_side")                   # seq_lens_cpu
        self.assertEqual(by_line[10]["kind"], "copy")                       # blocking .to(device)
        self.assertNotIn(11, by_line)                                       # non_blocking=True
        self.assertEqual(by_line[12]["call"], "torch.tensor(..., device=)")
        self.assertEqual(by_line[16]["temperature"], "cold")                # init_cuda_graph_state
        self.assertEqual((by_line[19]["call"], by_line[19]["temperature"]), (".synchronize()", "warm"))



FAKE_TMUX = """#!/bin/sh
echo "$*" >> "$FAKE/log"
case "$1" in
  has-session) [ -n "$FAKE_HAS" ]; exit $? ;;
  capture-pane) n=$(cat "$FAKE/n" 2>/dev/null || echo 0)
                if [ -f "$FAKE/screen$n" ]; then cat "$FAKE/screen$n"; else cat "$FAKE/last"; fi
                echo $((n + 1)) > "$FAKE/n" ;;
  load-buffer) cat > "$FAKE/buffer" ;;
esac
exit 0
"""


class RunInTmuxTest(unittest.TestCase):
    """scripts/run_in_tmux.sh against a fake tmux whose pane shows the screens a test scripts."""

    READY = "❯ \n  ⏵⏵ bypass permissions on (shift+tab to cycle)\n"

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.d = Path(tmp.name)
        (self.d / "bin").mkdir()
        tmux = self.d / "bin" / "tmux"
        tmux.write_text(FAKE_TMUX)
        tmux.chmod(0o755)
        self.task = self.d / "task.txt"
        self.task.write_text("use /workspace/inferpilot/inferpilot.js with mode=scan, inferpilot_dir=/workspace/inferpilot\n")

    def run_script(self, screens, has=""):
        (self.d / "n").unlink(missing_ok=True)
        for i, s in enumerate(screens):
            (self.d / ("screen%d" % i)).write_text(s)
        (self.d / "last").write_text(screens[-1])
        env = {"PATH": "%s:/usr/bin:/bin" % (self.d / "bin"), "FAKE": str(self.d), "FAKE_HAS": has,
               "POLL_S": "0", "TRIES": "8", "INFERPILOT_HOME": "/workspace/inferpilot"}
        r = subprocess.run(["bash", str(SCRIPTS / "run_in_tmux.sh"), str(self.task)], env=env,
                           capture_output=True, text=True, stdin=subprocess.DEVNULL)
        log = (self.d / "log").read_text().splitlines()
        return r.returncode, log, r.stderr

    def test_starts_claude_code_and_hands_over_the_task_once(self):
        rc, log, err = self.run_script(["", self.READY])
        self.assertEqual(rc, 0, err)
        self.assertIn("new-session -d -s inferpilot -x 200 -y 50 bash -i", log)
        self.assertIn("send-keys -t inferpilot cd /workspace/inferpilot && IS_SANDBOX=1 claude --dangerously-skip-permissions Enter", log)
        self.assertEqual(log[-3:], ["load-buffer -b inferpilot-task -", "paste-buffer -p -d -b inferpilot-task -t inferpilot",
                                    "send-keys -t inferpilot Enter"])
        self.assertEqual((self.d / "buffer").read_text(),
                         "use /workspace/inferpilot/inferpilot.js with mode=scan, inferpilot_dir=/workspace/inferpilot")

    def test_an_existing_session_is_reused(self):
        rc, log, _ = self.run_script([self.READY], has="1")
        self.assertEqual(rc, 0)
        self.assertFalse([c for c in log if c.startswith("new-session")])

    def test_a_first_start_question_stops_it_unanswered(self):
        for screen in ("Quick safety check: Is this a project you trust?\n ❯ No, exit\n   Yes, I trust this folder\n",
                       "WARNING: Claude Code running in Bypass Permissions mode\n ❯ No, exit\n   Yes, I accept\n",
                       "Do you want to use this API key?\n"):
            (self.d / "log").unlink(missing_ok=True)
            rc, log, err = self.run_script([screen])
            self.assertEqual(rc, 3, screen)
            self.assertIn("claude-config", err)
            self.assertFalse([c for c in log if c.startswith(("send-keys -t inferpilot Down", "paste-buffer"))], screen)

    def test_a_login_prompt_means_no_key(self):
        for screen in ("Select a login method:\n", "Paste code here if prompted >"):
            rc, _, err = self.run_script([screen])
            self.assertEqual(rc, 4, screen)
            self.assertIn("no key", err)

    def test_gives_up_without_a_prompt(self):
        rc, log, _ = self.run_script(["loading"])
        self.assertEqual(rc, 5)
        self.assertFalse([c for c in log if c.startswith("paste-buffer")])


if __name__ == "__main__":
    unittest.main()
