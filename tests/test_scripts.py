"""Offline unit tests for the scan and audit scripts (no network, no GPU).

    python3 -m unittest discover -s tests -p 'test_*.py'
"""
import argparse
import io
import json
import os
import re
import shutil
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
import propose_knowledge  # noqa: E402
import run_lock  # noqa: E402
import scan_ci  # noqa: E402
import scan_issues  # noqa: E402
import scan_upstream  # noqa: E402


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


class DailyReportTest(unittest.TestCase):
    """The scan's daily report: checked before a human shares it, found again once shared. Read only."""

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

    def test_check_exit_codes(self):
        template = Path(__file__).resolve().parent.parent / "templates" / "daily_report.md"
        with tempfile.TemporaryDirectory() as d:
            leaky, long = Path(d) / "leaky.md", Path(d) / "long.md"
            leaky.write_text(daily_report.MARKER + "\nweights in /data/models/x\n")
            long.write_text(daily_report.MARKER + "\n" + "x" * (daily_report.ISSUE_LIMIT + 1))
            with redirect_stdout(io.StringIO()) as out, redirect_stderr(io.StringIO()):
                self.assertEqual(daily_report.main(["check", str(template)]), 0, "the template itself is public-safe")
                self.assertEqual(daily_report.main(["check", str(leaky)]), 3)
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

    def test_it_never_writes(self):
        src = (Path(__file__).resolve().parent.parent / "scripts" / "daily_report.py").read_text()
        self.assertNotRegex(src, r"method=|\"POST\"|\"PATCH\"|\"PUT\"|\"DELETE\"")


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
              */v1/models) echo '{"data": [{"id": "fake-model"}]}' ;;
              */server_info) echo '{"internal_states": [{"avg_spec_accept_length": 4.0}]}' ;;
            esac
            """))
        (fake / "evalscope").write_text(textwrap.dedent("""\
            #!/bin/bash
            echo "$*" >> "$CALLS"
            printf '%s\\n' "$@" > "$CALLS.argv"
            [ -n "${FAKE_NO_REPORT:-}" ] && exit 1
            while [ $# -gt 0 ]; do
              case "$1" in --work-dir) wd=$2; shift ;; --datasets) ds=$2; shift ;; esac
              shift
            done
            echo "work dir $wd"
            mkdir -p "$wd/run/reports/fake-model"
            echo '{"metrics": [{"name": "mean_acc", "score": 0.97, "num": 1319}]}' > "$wd/run/reports/fake-model/$ds.json"
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

    def propose(self, token="t", api=None):
        env = {k: v for k, v in os.environ.items() if k != "INFERPILOT_REPO_TOKEN"}
        if token:
            env["INFERPILOT_REPO_TOKEN"] = token
        with mock.patch.dict(os.environ, env, clear=True), redirect_stdout(io.StringIO()) as out, \
                redirect_stderr(io.StringIO()) as err, mock.patch.object(propose_knowledge, "api", side_effect=api):
            rc = propose_knowledge.main(["--run-dir", str(self.run), "--body", str(self.body), "--repo", "At1a8/inferpilot",
                                         "--inferpilot-dir", str(self.inferpilot), "--push-url", str(self.origin)])
        return rc, out.getvalue(), err.getvalue()

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
    """scripts/gpu_status.py on a fake KFD tree whose node order differs from the PCI order."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.kfd = Path(tmp.name) / "kfd"
        nodes = self.kfd / "topology" / "nodes"
        # Two CPU nodes, then the GPUs in node order: HIP 0 is node 2, whose PCI bus (0x75) comes last
        for node, gpu_id, bus in [(0, 0, 0), (1, 0, 0), (2, 111, 0x75), (3, 222, 0x05), (4, 333, 0x65)]:
            (nodes / str(node)).mkdir(parents=True)
            (nodes / str(node) / "gpu_id").write_text("%d\n" % gpu_id)
            (nodes / str(node) / "properties").write_text(
                "location_id %d\ndomain 0\ngfx_target_version %d\n" % (bus << 8, 90500 if gpu_id else 0))
        for pid, gpu_id, mib in [(4242, 222, 4096), (5151, 333, 100)]:
            (self.kfd / "proc" / str(pid)).mkdir(parents=True)
            (self.kfd / "proc" / str(pid) / ("vram_%d" % gpu_id)).write_text("%d\n" % (mib * 2 ** 20))

    def run_main(self, *args, env=None):
        out = io.StringIO()
        with redirect_stdout(out):
            rc = gpu_status.main(["--kfd", str(self.kfd), *args], env={} if env is None else env)
        return rc, json.loads(out.getvalue())

    def test_hip_numbers_follow_the_kfd_node_order_not_the_pci_address(self):
        rc, out = self.run_main()
        self.assertEqual(rc, 0)
        self.assertEqual([(r["hip"], r["node"], r["pci"], r["arch"]) for r in out["gpus"]],
                         [(0, 2, "0000:75:00.0", "gfx950"), (1, 3, "0000:05:00.0", "gfx950"), (2, 4, "0000:65:00.0", "gfx950")])
        self.assertEqual([(r["used_mib"], r["pids"]) for r in out["gpus"]], [(0, {}), (4096, {"4242": 4096}), (0, {})],
                         "100 MiB is below --min-mib")

    def test_a_check_fails_while_another_job_holds_a_gpu(self):
        rc, out = self.run_main("--check", "all")
        self.assertEqual((rc, [r["hip"] for r in out["busy"]]), (1, [1]), "any held GPU fails a check of the machine")
        self.assertEqual(self.run_main("--check", "0,2")[0], 0)
        self.assertEqual(self.run_main("--check", "1")[0], 1)
        rc, out = self.run_main("--check", "7")
        self.assertEqual((rc, out["missing"]), (1, [7]))
        rc, out = self.run_main("--check", "2", "--min-mib", "64")
        self.assertEqual((rc, out["busy"][0]["pids"]), (1, {"5151": 100}))

    def test_rocr_visible_devices_or_no_gpu_stops_it(self):
        rc, out = self.run_main("--check", "all", env={"ROCR_VISIBLE_DEVICES": "1,2"})
        self.assertEqual(rc, 2)
        self.assertIn("ROCR_VISIBLE_DEVICES=1,2", out["error"])
        shutil.rmtree(self.kfd / "topology")
        rc, out = self.run_main("--check", "all")
        self.assertEqual(rc, 2)
        self.assertIn("no GPU", out["error"])

    def test_unreadable_accounting_is_unknown_not_free(self):
        shutil.rmtree(self.kfd / "proc")
        rc, out = self.run_main("--check", "all")
        self.assertEqual((rc, out["unknown"]), (1, [0, 1, 2]))
        self.assertIn("unreadable", out["error"])
        (self.kfd / "proc" / "77").mkdir(parents=True)  # a process with nothing on the GPUs: all free
        self.assertEqual(self.run_main("--check", "all")[0], 0)
        if os.geteuid() != 0:  # root reads a file whatever its mode
            f = self.kfd / "proc" / "77" / "vram_333"
            f.write_text("0\n")
            f.chmod(0)
            rc, out = self.run_main("--check", "all")
            self.assertEqual((rc, out["unknown"]), (1, [2]), "HIP 2 is KFD node 4, whose file cannot be read")


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
             "client_script": str(self.client), "workload": "vllm-49406 c1", "sets": "gsm8k", "harness": str(self.harness),
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
        self.assertTrue(self.store(self.key(sets="", evalscope=""))["stored"], "performance alone needs no evalscope")

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
