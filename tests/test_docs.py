"""What the code reads and what the docs say stay in step: every environment variable that setup, the
container script and the runner read is in the READMEs, every other script names the ones it reads in
its own header, every variable the READMEs' tables list is read by something, the command-line flags
the READMEs and AGENTS.md give exist, the branch bundle the roles write is the one a retry or a
resume takes the task's commits from, with commands that do it, and the detached launcher of
knowledge/env_hygiene.md leaves the run's PID and exit status. Offline.

The roles and the doctrine also agree on G0: it runs only where a change has a switch, and each role that
reports it says what it reports without one.

    python3 -m unittest discover -s tests
"""
import ast
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from test_readme import EN, ROOT, ZH, table_names

# Set by the system or the shell, not by a user of InferPilot.
STANDARD = {"HOME", "PATH", "PWD", "SHELL", "USER", "SUDO_USER", "LOGNAME", "TMPDIR", "LANG", "TERM", "TZ"}
BASH = {"SECONDS", "RANDOM", "LINENO", "BASH_SOURCE", "BASH_REMATCH", "PIPESTATUS", "FUNCNAME", "OPTARG",
        "OPTIND", "IFS", "PPID", "UID", "EUID", "HOSTNAME", "BASHPID", "REPLY"}
# Their variables are the READMEs' to document; any other script documents its own.
USER_FACING = ["inferpilot/bootstrap.py", "setup.py", "scripts/run_docker.sh", "runtime/*.mjs"]
ENV_NAME = re.compile(r"^[A-Z][A-Z0-9_]*$")
HEREDOC = re.compile(r"<<-?\s*(\\?)(['\"]?)([A-Za-z_]\w*)\2")
FUNCTION = re.compile(r"^[ \t]*(?:function[ \t]+)?([A-Za-z_][\w-]*)[ \t]*\(\)[ \t]*\{", re.M)


def shell_code(text):
    """What bash expands: comments, single-quoted and $'...' strings and quoted heredocs left out. A $(...)
    inside double quotes is code again, with quotes of its own."""
    out, i, n, stack = [], 0, len(text), []  # '"': inside double quotes; a number: inside such a $( ), its depth
    while i < n:
        c = text[i]
        dq = bool(stack) and stack[-1] == '"'
        if not dq:
            line = text[text.rfind("\n", 0, i) + 1:i]
            heredoc = HEREDOC.match(text, i) if c == "<" and text[i - 1:i] != "<" and not text.startswith("<<<", i) \
                and line.count("((") <= line.count("))") else None
            if heredoc:
                body = text.find("\n", i)
                end = re.compile(r"^\s*%s$" % heredoc.group(3), re.M).search(text, body + 1) if body >= 0 else None
                stop = end.end() if end else n
                if not (heredoc.group(1) or heredoc.group(2)):
                    out.append(text[body:stop] if body >= 0 else "")
                i = stop
                continue
            if c == "'" or text.startswith("$'", i):
                j = i + (2 if c == "$" else 1)
                while j < n and text[j] != "'":
                    j += 2 if c == "$" and text[j] == "\\" else 1
                i = j + 1
                continue
            if c == "#" and (i == 0 or text[i - 1] in " \t\n;("):
                j = text.find("\n", i)
                i = n if j < 0 else j
                continue
        if c == "\\" and i + 1 < n:
            out.append(text[i:i + 2])
            i += 2
            continue
        if c == '"':
            stack.pop() if dq else stack.append('"')
        elif dq and text.startswith("$(", i) and not text.startswith("$((", i):
            stack.append(0)
            out.append("$(")
            i += 2
            continue
        elif stack and not dq and c in "()":
            if c == "(":
                stack[-1] += 1
            elif stack[-1] == 0:
                stack.pop()
            else:
                stack[-1] -= 1
        out.append(c)
        i += 1
    return "".join(out)


def runs_at(code):
    """Where each position of a script runs: in a function's body, where the function is first called (from
    the top level, or from a function called there); elsewhere, where it stands."""
    bodies = {}
    for m in FUNCTION.finditer(code):
        depth, j = 1, m.end()
        while j < len(code) and depth:
            depth += {"{": 1, "}": -1}.get(code[j], 0)
            j += 1
        bodies[m.group(1)] = (m.start(), m.end(), j)

    def inside(pos):
        names = [f for f, (_, a, b) in bodies.items() if a <= pos < b]
        return max(names, key=lambda f: bodies[f][1]) if names else None

    called = {}

    def first_call(name, seen=()):
        if name not in called:
            if name in seen:
                return len(code)
            start, _, _ = bodies[name]
            sites = [m.start() for m in re.finditer(r"(?<![\w-])%s(?![\w-])" % re.escape(name), code) if m.start() != code.find(name, start)]
            called[name] = min([s if inside(s) is None else first_call(inside(s), seen + (name,)) for s in sites] or [len(code)])
        return called[name]

    return lambda pos: pos if inside(pos) is None else first_call(inside(pos))


def shell_inputs(text):
    """Variables a script takes from its environment: read before it first sets them (when it runs), or never
    set, or set from themselves with a default (X=${X:-...})."""
    code = shell_code(text)
    when = runs_at(code)
    reads, sets = {}, {}
    for m in re.finditer(r"\$\{?[#!]?([A-Z][A-Z0-9_]*)|\bprintenv\s+([A-Z][A-Z0-9_]*)", code):
        name = m.group(1) or m.group(2)
        reads[name] = min(reads.get(name, len(code)), when(m.start()))
    for m in re.finditer(r"\(\((.*?)\)\)", code, re.S):  # (( X > 0 )) reads X without a $
        for name in re.findall(r"(?<![\w$])([A-Z][A-Z0-9_]*)(?!\w)", m.group(1)):
            reads[name] = min(reads.get(name, len(code)), when(m.start()))
    for m in re.finditer(r"(?<![\w$.{/-])([A-Z][A-Z0-9_]*)(?:\[[^\]]*\])?\+?=|"
                         r"\b(?:local|declare|typeset|readonly|for|mapfile|printf -v)\s+(?:-\w+\s+)*([A-Z][A-Z0-9_]*)\b|"
                         r"\bread\s+(?:-\w+\s+(?:\S+\s+)?)*([A-Z][A-Z0-9_ ]*)", code):
        for name in (m.group(3) or "").split() or [m.group(1) or m.group(2)]:
            sets[name] = min(sets.get(name, len(code)), when(m.start()))
    defaulted = set(re.findall(r"\b([A-Z][A-Z0-9_]*)=\"?\$\{\1:?[-=]", code))
    return {v for v, at in reads.items() if v not in sets or at < sets[v] or v in defaulted} - STANDARD - BASH


def python_inputs(text):
    """Keys the environment is read with: env.get/setdefault/pop(...), environ[...], getenv(...) and
    "X" in environ, under any name environ or getenv is imported or bound as. A key is a string, a module
    constant holding one, or the loop variable over a module's tuple of them (for v in IDENTITY_VARS);
    setting a variable for a child (environ["X"] = ...) is not reading it."""
    tree = ast.parse(text)
    strings, consts = {}, {}
    envs, getenvs = {"env", "environ"}, {"getenv"}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "os":
            envs |= {a.asname for a in node.names if a.name == "environ" and a.asname}
            getenvs |= {a.asname for a in node.names if a.name == "getenv" and a.asname}
        elif isinstance(node, ast.Assign) and isinstance(node.value, ast.Attribute) and node.value.attr == "environ":
            envs |= {t.id for t in node.targets if isinstance(t, ast.Name)}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in [t for t in node.targets if isinstance(t, ast.Name)]:
                if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                    strings[target.id] = node.value.value
                elif isinstance(node.value, (ast.Tuple, ast.List, ast.Set)):
                    consts[target.id] = [e.value for e in node.value.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)]
    looped = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.For, ast.comprehension)) and isinstance(node.iter, ast.Name) \
                and isinstance(node.target, ast.Name):
            looped.setdefault(node.target.id, []).extend(consts.get(node.iter.id, []))

    def is_env(n):
        return isinstance(n, ast.Name) and n.id in envs or isinstance(n, ast.Attribute) and n.attr == "environ"

    out = set()
    for node in ast.walk(tree):
        key = None
        if isinstance(node, ast.Call) and node.args:
            f = node.func
            if isinstance(f, ast.Attribute) and f.attr in ("get", "setdefault", "pop") and is_env(f.value) \
                    or isinstance(f, ast.Attribute) and f.attr == "getenv" or isinstance(f, ast.Name) and f.id in getenvs:
                key = node.args[0]
        elif isinstance(node, ast.Subscript) and is_env(node.value) and isinstance(node.ctx, ast.Load):
            key = node.slice
        elif isinstance(node, ast.Compare) and any(isinstance(o, (ast.In, ast.NotIn)) for o in node.ops) \
                and any(is_env(c) for c in node.comparators):
            key = node.left
        if isinstance(key, ast.Constant) and isinstance(key.value, str):
            out.add(key.value)
        elif isinstance(key, ast.Name):
            out.update(looped.get(key.id, []) + ([strings[key.id]] if key.id in strings else []))
    return {n for n in out if ENV_NAME.match(n)} - STANDARD


def js_inputs(text):
    names = {a or b or c for a, b, c in re.findall(
        r"process\.env\.([A-Z][A-Z0-9_]*)|process\.env\[['\"]([A-Z][A-Z0-9_]*)|binEnv: '([A-Z][A-Z0-9_]*)'", text)}
    for group in re.findall(r"\{([^{}]*)\}\s*=\s*process\.env\b", text):  # const { A, B: b } = process.env
        names |= {part.split(":")[0].strip() for part in group.split(",")} - {""}
    return {n for n in names if ENV_NAME.match(n)} - STANDARD


def argparse_flags(path):
    """Every --flag an add_argument(...) call of the file declares, in any of its names."""
    return {a.value for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
            if isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "add_argument"
            for a in node.args if isinstance(a, ast.Constant) and isinstance(a.value, str) and a.value.startswith("--")}


def inputs(path):
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".sh":
        return shell_inputs(text)
    if path.suffix == ".py":
        return python_inputs(text)
    return js_inputs(text)


def code_files():
    return sorted(p for pattern in ("inferpilot/*.py", "setup.py", "scripts/*.py", "scripts/*.sh", "templates/harness/*.sh",
                                    "runtime/*.mjs")
                  for p in ROOT.glob(pattern) if not p.name.startswith("test_"))


def header(path):
    """A script's own documentation: its docstring, or the comments it opens with."""
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".py":
        return ast.get_docstring(ast.parse(text)) or ""
    mark = "//" if path.suffix == ".mjs" else "#"
    lines = text.splitlines()
    top = []
    for line in lines[1:] if lines and lines[0].startswith("#!") else lines:
        if not line.startswith(mark):
            break
        top.append(line)
    return "\n".join(top)


def mentions(name, text):
    return re.search(r"(?<![A-Za-z0-9_])%s(?![A-Za-z0-9_])" % re.escape(name), text) is not None


def flags_in(text, command):
    """The --flags given to command: from where it is named to the end of its code span or command."""
    text = text.replace("\\\n", " ")
    out = set()
    for m in re.finditer(re.escape(command), text):
        rest = re.split(r"`|\n|&&|;|\|", text[m.end():], maxsplit=1)[0]
        out |= set(re.findall(r"(?<![\w-])--[a-z][a-z0-9-]*", rest))
    return out


class TokenComparisonTest(unittest.TestCase):
    """G2 compares scores, not tokens: a token comparison is a diagnosis aid or the subject of a determinism
    task (knowledge/validation_doctrine.md). No other document asks a gate for outputs equal to another
    run's, which a bf16 baseline does not give twice."""

    ASKS = re.compile(r"output\s+parity|match(?:es)?\s+greedy\s+output|greedy\s+outputs?\s+(?:equal|identical|match\w*)", re.I)

    def test_no_gate_asks_for_output_parity(self):
        doctrine = " ".join((ROOT / "knowledge" / "validation_doctrine.md").read_text(encoding="utf-8").split())
        self.assertIn("never the accuracy gate", doctrine)
        found = ["%s: %s" % (path.relative_to(ROOT), " ".join(m.group(0).split()))
                 for pattern in ("knowledge/*.md", "roles/*.md", "templates/*.md") for path in sorted(ROOT.glob(pattern))
                 for m in self.ASKS.finditer(path.read_text(encoding="utf-8"))]
        self.assertEqual(found, [], "these ask for outputs equal to another run's; G2 compares scores")


class CiExistingFixTest(unittest.TestCase):
    """A CI failure that an open pull request already fixes is verified, not fixed a second time
    (knowledge/ci_triage.md §2), as an issue with an open fix PR is: the classification names the pull request,
    the run applies it without a bisect, the failing test is its gate, and the PR phase drafts a verification
    comment for it. A pull request runs its author's code on this machine: the run applies one only on the
    user's word, whoever wrote it, and only one the classification found open, at the commit its branch is at."""

    @staticmethod
    def flat(path):
        return " ".join((ROOT / path).read_text(encoding="utf-8").split())

    def test_the_run_applies_one_pull_request_on_the_users_word_without_a_bisect(self):
        src = (ROOT / "inferpilot.js").read_text(encoding="utf-8")
        body = re.search(r"\nconst CLASSIFY = S\(\{(.*?)\n\}, \[", src, re.S).group(1)
        self.assertIn("existing_prs: arr(S({ pr: str, repo: str, author: str, association: str, head: str, state: str }, ['pr'])),", body,
                      "the classification cannot say who wrote an open fix, which repository and commit it is at, or whether it is still open")
        for said, why in (
                # the user's word is what the caller gave: the field or, where it is blank, the answer; not the Intake's reading
                ("const field = prWords(RAW.apply_prs);\n  const said = field.length ? field : prWords(asObject(RAW.answers).apply_prs);\n"
                 "  return { said, read: said.length ? [] : prWords(A.apply_prs) };", "what the Intake read into a request passes for the user's word"),
                ("const isPr = (p) => /^#\\d+$/.test(p);", "a pull request of another repository is applied to this tree"),
                ("const bare = /^(?:pr\\s*|pull request\\s*)?#?(\\d+)$/i.exec(p);", "an answer that gives the bare number is dropped"),
                # a word that names its repository keeps it: its number alone names another pull request in the framework's
                ("return m ? `${m[1]}#${m[2]}` : '';", "a URL is cut down to its number, which names another pull request in the framework's repository"),
                ("return p.pr === ref.slice(at) && (!repo || p.repo === repo);", "a word for another repository's pull request runs the framework's"),
                # the word decides when it names a pull request or says none, and one of the two only
                ("const none = said.some((w) => /^\"?none\"?$/i.test(w));\n  const decided = none ? !byUser.length : byUser.length > 0;",
                 "an answer that names nothing is taken for none"),
                ("const usable = here.filter((p) => p.state === 'open' && p.head);", "a closed or a merged pull request, or one at no known commit, is applied"),
                ("const apply = decided ? byUser.map((ref) => usable.find((p) => samePr(ref, p))).find(Boolean) || null : null;",
                 "a pull request the user did not name is applied, or several are applied together"),
                ("if (!decided && candidates.length) {", "the user is not asked, is asked again after deciding, or is not asked again after an "
                                                         "answer that decides nothing"),
                ("named_prs: MODE === 'ci' ? [...namedPrs().said, ...namedPrs().read].map((p) => prRef(p)).filter(Boolean) : [],",
                 "Classify is not told which pull requests the task names, with their repositories, to look them up"),
                ("out.rerun_with = { apply_prs: '', answers: { apply_prs: null } };", "a field the task left blank wins over the answer, and the run asks again"),
                ("const offered = (def) => Object.fromEntries(Object.entries(pick(def, TASK_KEYS))\n"
                 "      .filter(([k]) => !(k === 'apply_prs' && String(def.mode).trim().toLowerCase() === 'ci')));",
                 "a scan offers a ci task with a pull request to apply, as a field or as an answer a model wrote"),
                # an issue's existing fix: one pull request, and the roles after it know which
                ("const prs = listed[0] || '';\n    if (prs) C.existing_prs = [prs];", "an issue's verification ends in a push command, or applies several")):
            self.assertIn(said, src, why)
        self.assertNotIn("MAINTAINERS", src, "the run still applies a pull request on what a model says of its author")
        ci = src[src.index("async function runCi() {"):src.index("async function runPerf() {")]
        for said, why in (
                ("const fix = existingFix(cl.existing_prs);", "the open fix is applied without the user's word"),
                ("if (stopHere('Bisect')) {", "a stop named for the phase an existing fix skips does not hold"),
                ("R.questions = [fix.ask.question];", "the run does not ask before it runs another author's code"),
                ("const lacking = askForFix('Classify');", "an existing fix is applied without the model and the client its gates need"),
                ("C.existing_prs = [pr];", "the roles after Classify do not know which pull request is under verification"),
                ("kind: 'apply_pr', goal: `apply the existing fix ${pr} at ${head} and complete what verification needs`",
                 "the pull request is not applied as a work package, or not at the commit the classification read"),
                ("return deliver([wp], gates, ciClaim, perfFailure ? true : 'fix');", "the existing fix is not held to the failing test and the fix's gates")):
            self.assertIn(said, ci, why)
        self.assertLess(ci.index("const fix = existingFix(cl.existing_prs);"), ci.index("enter('Bisect');"), "the culprit is searched for although a fix is open")
        self.assertLess(ci.index("if (handOver) return finish('handed_off', 'Classify');"), ci.index("const fix = existingFix(cl.existing_prs);"),
                        "a failure that is handed over is fixed all the same")
        self.assertLess(ci.index("if (fix.ask) {"), ci.index("if (fix.apply) {"), "the question comes after the pull request is applied")
        # The run ends in a comment for the pull request, and the user is told to post it, not to push a branch.
        for said in ("...((C.existing_prs || []).length ? { comment_for: C.existing_prs } : {}) };",
                     "if (verified) user.push(`post the verification comment on ${verified}, once you have re-run what it cites: ${R.pr.pr_body_path}`);"):
            self.assertIn(said, src)

    def test_the_documents_say_it(self):
        for path, said in (
                ("knowledge/ci_triage.md", "**A failure an open pull request already fixes is verified, not fixed again.**"),
                ("knowledge/ci_triage.md", "a pull request that cites a failure does not always fix it"),
                ("knowledge/ci_triage.md", "the failing test is its gate, on every lane it failed on, and its accuracy and speed are measured by what it changes"),
                ("knowledge/ci_triage.md", "It ends in a verification comment for that pull request, drafted for a human to post"),
                ("knowledge/ci_triage.md", "at which commit of the pull request"),
                ("knowledge/ci_triage.md", "No second pull request is opened for the same failure"),
                ("knowledge/ci_triage.md", "A fix that has merged needs none of this: main has it"),
                # whose code the run executes
                ("knowledge/ci_triage.md", "Verifying a pull request runs its author's code and tests on this machine, next to the keys the agents hold"),
                ("knowledge/ci_triage.md", "The run applies one only on the user's word, whoever wrote it: it stops after Classify and asks which one "
                                           "to verify, or none"),
                ("knowledge/ci_triage.md", "What the classification says of the author is no proof that the author can put code on main"),
                ("knowledge/ci_triage.md", "the run has no way of its own to look the permission up"),
                ("knowledge/ci_triage.md", "What the Intake read into a request in plain words is a reading too, and is asked about: only a field or "
                                           "an answer the caller gave counts"),
                ("knowledge/ci_triage.md", "A task a scan offers names no pull request to apply"),
                ("knowledge/ci_triage.md", "the run applies only a pull request found open, with the commit its branch is at"),
                ("knowledge/ci_triage.md", "One that is closed, merged or not found is not applied, the triage record says why, and the run goes on "
                                           "to a fix of its own"),
                ("knowledge/ci_triage.md", "One pull request is verified in a run: several applied together would show nothing of each"),
                ("roles/ci_guardian.md", "4. Look for a fix someone has already opened"),
                ("roles/ci_guardian.md", "Read the diff of each, and run nothing of it"),
                ("roles/ci_guardian.md", "the fix a tracker names can be wrong"),
                ("roles/ci_guardian.md", "the author's association with the repository as the API gives it (`author_association`), its head commit "
                                         "and its `state` (`open`)"),
                ("roles/ci_guardian.md", "the repository it is in (`repo`: `<owner>/<repo>`, as the API gives it)"),
                # a name that carries its repository is looked up there, never by its number in the framework's
                ("roles/ci_guardian.md", "A name that carries its repository (`<owner>/<repo>#<n>`, from a URL the user gave) is looked up in that "
                                         "repository, never by its number in the framework's"),
                ("roles/ci_guardian.md", "Return it as `#<n>` only when that repository is the framework's, and always with its `repo`"),
                ("knowledge/ci_triage.md", "A word that names its repository (a URL, `<owner>/<repo>#<n>`) keeps it"),
                ("knowledge/ci_triage.md", "never for the framework's pull request of the same number"),
                ("knowledge/ci_triage.md", "it is taken by the fields of a task definition, and an answer or a request a model wrote next to them "
                                           "goes no further"),
                ("knowledge/ci_triage.md", "The word has to decide: it names a pull request, or it says `none`"),
                ("knowledge/ci_triage.md", "Any other answer, or both at once, is asked about again"),
                ("roles/ci_guardian.md", "It does so only on the user's word, whoever the author is: the association is for the user to read, and "
                                         "proves no right to put code on main"),
                ("roles/ci_guardian.md", "The pull requests in `named_prs` are the ones the task or the request names for verification: look each "
                                         "of them up"),
                ("roles/ci_guardian.md", "with its `state` as the API gives it now (`open`, `closed`, `merged`, or `not_found`)"),
                ("roles/ci_guardian.md", "One that is a pull request of another repository goes under its full name (`<owner>/<repo>#<n>`), never "
                                         "as this repository's `#<n>`"),
                ("roles/ci_guardian.md", "The run applies only one returned `open` with its head"),
                ("roles/ci_guardian.md", "One that has merged is not listed"),
                ("roles/ci_guardian.md", "One in another repository (a component's) is not listed either"),
                ("roles/ci_guardian.md", '"existing_prs": [{"pr": "#<n>", "repo": "<owner>/<repo>", "author": "<login>", '
                                         '"association": "<author_association>", "head": "<sha>", "state": "open | closed | merged | not_found"}],'),
                ("roles/implementer.md", "Write the head you fetched in the ledger (`git rev-parse FETCH_HEAD`): results are for the PR at that commit"),
                ("roles/implementer.md", "What you add (a missing test, a fix on top) is commits of your own after the author's"),
                # a conflict resolved in a cherry-pick is part of that commit: said, not hidden
                ("roles/implementer.md", "a conflict resolved during a cherry-pick is staged before `git cherry-pick --continue`, and so is part of "
                                         "that commit"),
                ("roles/implementer.md", "the verification comment says that this commit is not the author's as pushed"),
                ("roles/implementer.md", 'write that tip in the ledger as well (`git rev-parse HEAD`, "the PR as applied")'),
                # a retry that takes the package from the restored ref records this attempt's tip between the author's commits and its own
                ("roles/implementer.md", "An `apply_pr` package is taken from the ref in two parts"),
                ("roles/implementer.md", 'first the commits up to that one, the author\'s; then write this attempt\'s tip in the ledger (`git '
                                         'rev-parse HEAD`, "the PR as applied"); then the commits after it, the run\'s additions'),
                ("roles/implementer.md", "take nothing from the ref for this package: apply the PR from step 3, which records the tip"),
                ("roles/test_engineer.md", "the commit the package was made on: the last commit of `verified_work_packages`, or `<base>` for the first package"),
                # the feature-off comparison takes the same tree before the package as the control does
                ("roles/test_engineer.md", "The parent is the tree before the package, here as in step 3"),
                ("roles/test_engineer.md", "where a test an earlier commit of the package broke fails already and would pass for unchanged"),
                # the PR engineer rewrites nothing of the author's, and exports the run's additions apart
                ("roles/pr_engineer.md", "A run that verified an existing PR (`existing_prs`) rewrites nothing of it"),
                ("roles/pr_engineer.md", "Only what the run added after that tip is tidied, in commits of its own that stay after it"),
                ("roles/pr_engineer.md", "A commit of the PR whose conflict the implementer resolved while applying it (the ledger names each) is "
                                         "not the author's as pushed"),
                ("roles/pr_engineer.md", "format-patch <that tip>..inferpilot/<slug> -o RUN_DIR/patches_additions"),
                ("roles/pr_engineer.md", "a series that carried the PR's own commits would apply them a second time"),
                ("roles/pr_engineer.md", "that is the whole of what was verified, to replay it, not what the comment offers"),
                ("knowledge/pr_playbook.md", "It is for one PR: a run verifies one (for an issue the first its triage lists, for a CI failure the one "
                                             "the user names)"),
                ("knowledge/pr_playbook.md", "The run's result says to post the comment, and gives no push command"),
                ("roles/pr_engineer.md", "(an issue triaged `push_existing_pr`, a CI failure classified with `existing_prs`)"),
                ("roles/pr_engineer.md", "It names the commit of the PR that was verified"),
                ("roles/pr_engineer.md", "lists apart what the run added on top of it, commit by commit and why"),
                ("roles/pr_engineer.md", "None for an existing PR's verification (`push_command` empty)"),
                ("templates/ci_triage.md", "| open fix PRs |"),
                ("templates/task_definition.md", "`ci`: the open fix PR the run may verify (`#<n>`), or `none`"),
                ("README.md", "A CI failure that an open pull request already fixes skips Bisect"),
                ("README.md", "It runs that pull request's code only on your word (`apply_prs`), and asks when the task does not say"),
                ("README.md", "so the run applies one only when it is named here, whoever wrote it; without it the run asks"),
                ("README.zh-CN.md", "那个 PR 的代码只有在你点名（`apply_prs`）之后才会在本机运行，任务里没说时运行会先问"),
                ("README.zh-CN.md", "所以不论作者是谁，只有在这里点名的 PR 才会应用；没给时运行会先问"),
                ("templates/task_definition.md", "so the run applies one only when it is named here, whoever wrote it"),
                ("README.md", "`ci`: the open fix PR the run may verify (`#<n>`), or `none`"),
                ("README.zh-CN.md", "CI failure 已经有人提了修复 PR 且还开着时，跳过 Bisect"),
                ("README.zh-CN.md", "`ci`：允许运行去验证的那个已有修复 PR（`#<n>`），或 `none`")):
            self.assertIn(said, self.flat(path), "%s does not say it" % path)
        self.assertIn("args.apply_prs (perf: candidate PRs to apply; ci: the open fix PR the run may verify, or none",
                      (ROOT / "inferpilot.js").read_text(encoding="utf-8").split("\n")[3], "whenToUse does not tell the caller of it")
        # The steps of the classification are numbered through: the new one did not leave two fourth steps.
        role = (ROOT / "roles" / "ci_guardian.md").read_text(encoding="utf-8")
        classify = role[role.index("## PHASE=classify"):role.index("## PHASE=bisect")]
        self.assertEqual(re.findall(r"^(\d+)\. ", classify, re.M), ["1", "2", "3", "4", "5"])


class EnvironmentTest(unittest.TestCase):
    def test_setup_container_and_runner_variables_are_in_both_readmes(self):
        missing = ["%s reads %s; %s does not say what it does" % (path.relative_to(ROOT), name, readme)
                   for pattern in USER_FACING for path in sorted(ROOT.glob(pattern)) for name in sorted(inputs(path))
                   for readme, text in (("README.md", EN), ("README.zh-CN.md", ZH)) if not mentions(name, text)]
        self.assertEqual([], missing)

    def test_every_other_script_names_the_variables_it_reads(self):
        facing = {p for pattern in USER_FACING for p in ROOT.glob(pattern)}
        missing = ["%s reads %s; neither its header nor the README says what it does" % (path.relative_to(ROOT), name)
                   for path in code_files() if path not in facing for name in sorted(inputs(path))
                   if not mentions(name, header(path)) and not mentions(name, EN)]
        self.assertEqual([], missing)

    def test_every_variable_the_readme_tables_list_is_read(self):
        read = set().union(*(inputs(p) for p in code_files()))
        self.assertEqual([], ["README.md lists %s, which nothing reads" % name
                              for name in sorted(table_names(EN)) if ENV_NAME.match(name) and name not in read])


class ExistingFixFailsTest(unittest.TestCase):
    """An existing fix PR is verified, not repaired (knowledge/issue_triage.md §4). Where it does not pass, the
    run drafts the comment that says so for that pull request, takes it off the task branch and fixes the
    failure itself; the comment is written whatever comes of that fix."""

    @staticmethod
    def flat(path):
        return " ".join((ROOT / path).read_text(encoding="utf-8").split())

    def test_the_run_falls_back_to_a_fix_of_its_own(self):
        src = (ROOT / "inferpilot.js").read_text(encoding="utf-8")
        for said, why in (
                ("const PR_COMMENT = S({ comment_path: str, summary: str, branch_findings: str }, ['comment_path']);",
                 "the comment has no answer to give, or cannot say what the leak scan found on the pull request's branch"),
                ("async function existingFailed(prs, at, why) {", "nothing happens when an existing fix does not pass"),
                # what the PR phase prepared from the discarded branch is not the run's output
                ("  delete R.pr;\n  Object.assign(C, { pr_body_path: '', pr_work_packages: [], leak_scan_findings: [] });",
                 "a pull request prepared before the fall-back is still reported as the run's"),
                # what the comment phase says besides the path is kept with the result
                ("if (summary) C.existing_pr_result.comment_summary = summary.slice(0, 600);", "what the comment says is dropped"),
                ("C.existing_pr_result.branch_findings = found;", "what the leak scan found on the pull request's branch never reaches the report"),
                ("const cm = await call('pr_engineer', 'comment', inputs({ failed_at: at, failure: why }), PR_COMMENT);", "no comment is drafted for the pull request"),
                ("ROLLBACK, 'implementer:rollback:existing');", "the pull request stays on the branch the run's own fix is made on"),
                ("R.done.length = 0;", "the pull request's package still counts as a verified one"),
                ("async function verifyLoop(extraGates, existing) {", "the verify loop does not know that the fix is someone else's"),
                # only what is shown to be the pull request's counts against it
                ("if (existing && inv.in_additions !== true\n"
                 "      && (inv.verdict === 'fix_needed' || (inv.verdict === 'inconclusive' && inv.attributed === true))) {",
                 "what the pull request lacks is repaired on top of it, a comparison that decided nothing counts against it, or a failure "
                 "in what the run added does"),
                ("attributed: bool,", "the investigator cannot say that the controls attribute a failure it cannot explain"),
                ("in_additions: bool,", "the investigator cannot say that what fails is what the run added on top of the pull request"),
                # what the run added on top is the run's to repair, and its failure is never the pull request's
                ("failed_in: { type: 'string', enum: ['pr', 'additions', ''] },", "the unit gate cannot say whose failure it is"),
                ("        if (existing && ut.failed_in === 'pr') {\n          theirs = true;\n          break;\n        }",
                 "a gate the pull request failed is repaired in a later round, or a failure of the run's own test is held against it"),
                ("        if (existing && ut.failed_in !== 'additions') {", "a gate that does not say whose failure it is goes on to a repair round"),
                ("time_limit_min: exactMinutes(timeoutFor('test_engineer')), ...(existing ? { verify_only: true } : {}),\n"
                 "        ...(over ? { added_to: over } : {}) };", "the test engineer is not told that the package is someone else's pull request, "
                                                                    "or added on top of one"),
                # the run's own packages on top of the pull request: repaired like any, and only what their gate places in the pull request is its
                ("async function implementAll(wps, existing, over) {", "a package added on top of the pull request is not known for one"),
                ("        if (over && ut.failed_in === 'pr') {\n          theirs = true;\n          break;\n        }",
                 "a defect of the pull request that an addition's gate shows is repaired on the author's commits, or ends the run without its comment"),
                ("const fix = await implementAll(wps, undefined, existing);\n"
                 "    if (!fix.ok) return { ok: false, at: 'Implement', ...(fix.theirs ? { theirs: true, reason: fix.reason } : {}) };",
                 "the unit gate of a review addition forgets the pull request"),
                ("const fix = await implementAll(pr.wps, undefined, existing);\n"
                 "    if (!fix.ok) return fix.theirs ? failed({ theirs: true, reason: fix.reason }, 'Implement') : finish('blocked', 'Implement');",
                 "the unit gate of a PR-phase addition forgets the pull request"),
                ("const fix = await implementAll(inv.work_packages, undefined, existing);\n", "the unit gate of a repair to the additions "
                                                                                             "forgets the pull request"),
                # such a failure was found in Implement: the fall-back is recorded there, and a stop named for Implement holds there
                ("      if (!fix.ok) return fix.theirs ? { ...theirs(fix.reason), at: 'Implement' } : { ok: false, reason: `fix ${fix.failed_wp || ''} "
                 "failed: ${fix.reason}` };", "a defect of the pull request that a repair's gate shows is recorded as a Verify failure"),
                # the pull request stays on the branch until its comment is drafted
                ("    if (!passed && theirs) {\n", "the pull request's package is rolled back before the comment for it reads the branch"),
                ("return existing && inv.in_additions === true ? { ok: false, reason } : theirs(reason);",
                 "a failure of the fix's own gate that is the additions' is held against the pull request"),
                ("integrated_kernels: integratedKernels, ...(existing ? { verify_only: true } : {}) }),",
                 "the investigator is not told that the change is someone else's pull request"),
                # a failure of the pull request found in a repair round is still the pull request's
                ("async function reviewLoop(extraGates, perfClaim, runPerf, existing) {", "the review's repair round forgets the pull request"),
                ("async function repaired(extraGates, perfClaim, runPerf, existing) {", "a repair round forgets the pull request"),
                ("const ver = await verifyLoop(extraGates, existing);\n  // A failure the round attributes to someone else's pull request goes back as such",
                 "a repair round verifies someone else's pull request as a fix of the run's own"),
                ("return ver.stopped ? ver : { ok: false, at: ver.at || 'Verify', ...(ver.theirs ? { theirs: true, reason: ver.reason } : {}) };",
                 "a failure of the pull request found in a repair round ends the run without its comment"),
                ("const failed = (r, at) => (existing && r.theirs ? existingFailed(existing, r.at || at, r.reason) : ended(r, at));",
                 "a round that ends on a failure of the pull request is not answered with the comment"),
                ("const rev = await reviewLoop(extraGates, perfClaim, runPerf, existing);\n  if (!rev.ok) return failed(rev, rev.at);",
                 "the review round is not told of the pull request"),
                ("const stop = await repaired(extraGates, perfClaim, runPerf, existing);\n    if (stop) return failed(stop, stop.at);",
                 "the PR phase's round is not told of the pull request"),
                ("if (!impl.ok) return existing && impl.theirs && !impl.held ? existingFailed(existing, 'Implement', impl.reason) : finish('blocked', 'Implement');",
                 "a pull request that does not apply ends the run, one whose gate may still be running is rolled back, or a missing answer "
                 "counts against it"),
                ("...(existing ? { verify_only: true } : {}), ...(over ? { added_to: over } : {}) }), IMPL, imLabel);",
                 "the implementer is not told to leave the pull request as it is, or that its package is added on top of one"),
                ("conflicts: strs,", "the implementer cannot say that the pull request no longer applies"),
                ("if (existing && conflicts.length) {", "a package blocked for another reason counts as the pull request's failure"),
                # nothing is rolled back or fixed next to what may still be running
                ("}, ['wp', 'unit_pass', 'feature_off_unchanged', 'fails_without_change', 'incomplete', 'left_running']);",
                 "a unit answer that leaves out what it could not stop is taken for a gate that cleaned up"),
                ("if (leftBy(ut)) return gateLeftRunning(wp, im.commit, leftBy(ut));", "a call to finish the gate, a fix round or a rollback starts "
                                                                                      "next to what the gate could not stop"),
                ("if (leftBy(fin)) return gateLeftRunning(wp, im.commit, leftBy(fin));", "the call that finishes a gate is not held to the same"),
                ("const leftOn = String(v.left_running || '').trim();", "a Verify round cannot say what it could not stop"),
                ("      if (!passedRound) {\n        const why = `the round could not stop what it started (${leftOn}): nothing is investigated, "
                 "repaired or rolled back next to it`;", "an investigation or a rollback starts next to what a failed round could not stop"),
                # a failed round that does not say whether it cleaned up: the GPUs are checked before anything else works on them
                ("      if (typeof answer.left_running === 'string' || asked.has(answer)) return !unconfirmed;\n"
                 "      const label = `verify_engineer:gpus#after${round}${asked.size ? '+time' : ''}`;\n      asked.add(answer);\n"
                 "      const after = await call('verify_engineer', 'gpus', inputs({ round: `after ${round}` }), ROUND_CLEANUP, label);",
                 "a round that does not say whether it cleaned up is taken for one that did"),
                ("    const refused = () => stopped(v);", "a round that is refused is not asked what it left"),
                # the check says what it could not stop, in every answer: a process that holds no GPU shows nowhere else
                ("const ROUND_CLEANUP = S({ in_use: str, in_use_gpus: arr({ type: ['string', 'integer'] }), left_running: str },\n"
                 "  ['in_use', 'in_use_gpus', 'left_running']);", "idle GPUs are taken for a round that stopped everything, or the GPUs in use "
                                                                 "are read from words"),
                ("      else if (left) unconfirmed = `${unsaid}the check after it could not stop ${left}`;",
                 "what the check could not stop is investigated next to"),
                # the GPUs are compared by their numbers: another job on one of them excuses that one alone
                ("const gpusNamed = (s) => [...new Set([...String(s || '').matchAll(/\\bGPU\\s+(\\d+)\\s*:/g)].map((m) => m[1]))];",
                 "the GPUs an answer names are not read by their numbers"),
                ("      const fresh = ids.filter((g) => !gpusNamed(inUse).includes(g));",
                 "a job on one GPU before the round excuses a server the round left on another"),
                ("      const unread = ids.some((g) => !/^\\d+$/.test(g)) || (!!held && !ids.length) || gpusNamed(held).some((g) => !ids.includes(g));",
                 "a GPU in use that the list does not give by its number is taken for none"),
                # a round that asks for more time is run again at once: one that does not say what it left is checked first
                ("    const needs = limit && r ? Math.ceil(Number(await needsOf(r)) || 0) : 0;", "a caller cannot check anything before a round is run again"),
                ("        return Number(r.needs_min) > 0 && (await stopped(r)) ? r.needs_min : 0;",
                 "a round that does not say what it left is run again next to it"),
                ("    const wantsTime = v.all_pass !== true && Number(v.needs_min) > 0 && !(v.failures || []).some((f) => f.blocking !== false);\n"
                 "    if (wantsTime && (leftOn || unconfirmed)) {", "a round held for what it left is investigated as a failure, or passes on "
                                                                    "failures that do not block with gates it never ran"),
                ("    if (!passedRound) await refused();", "a failed round is not asked whether it cleaned up"),
                # a pass that is refused is asked the same: no script for a model with no text answers, no comparison, no score
                ("    if (C.server_code_changed && outputsLacking()) {\n      await refused();", "a refused pass is taken for a round that cleaned up"),
                ("      && (comparisonFailed || !(/^pass\\b/i.test(compared) || keptOutputs))) {\n      await refused();",
                 "a pass without its output comparison is taken for a round that cleaned up"),
                ("    if (C.accuracy_claim && (v.all_pass === true || (failures.length && !blocking.length)) && !/^pass\\b/i.test(claimGate)) {\n"
                 "      await refused();", "a pass without the score is taken for a round that cleaned up"),
                ("      else if (fresh.length) unconfirmed = `${unsaid}GPU ${fresh.join(', ')}, not in use before it, is in use after it (${held})`;",
                 "a GPU the round still holds is investigated next to"),
                ("        leftRunning.push(unconfirmed);", "the closing phases launch on GPUs the run could not confirm free"),
                ("    if (unconfirmed) {\n      note('Verify', 'fail', `${unconfirmed}: nothing is investigated or repaired next to it`);",
                 "an investigation starts where the round may still hold the GPUs"),
                ("        if (String(r.left_running || '').trim() || r.all_pass === true || (r.failures || []).some((f) => f.blocking !== false)) return 0;",
                 "a round that could not stop what it started is run again with more time next to it"),
                ("const live = [...TIMED_OUT_CALLS].map((c) => `${c} timed out and may still be running`).concat(leftRunning);\n  if (live.length) {",
                 "a rollback starts next to a call that timed out, or to what a round left running"),
                # the run's own fix starts on the task's base and on no other commit
                ("base_commit: str,", "Setup cannot return the commit the task branch was made on"),
                ("C.base_commit = String(s.base_commit || '').trim();", "the run does not know the task's base"),
                ("if (!sameCommit(target, base)) {", "the run's own fix starts on a branch that is not at the task's base"),
                # a stop named for the phase the pull request failed in holds
                ("if (stopHere(at)) {\n    note(at, 'stopped', `stop_after=${at}: ${prs} did not pass", "the run's own fix starts past the phase the operator named"),
                # the run's own fix is prepared as any fix, at the priority the problem has without a usable fix PR
                ("C.existing_prs = [];", "the PR phase writes a verification comment for the run's own fix"),
                ("priority_without_pr: { type: 'string', enum: ['P0', 'P1', 'P2', ''] },", "Triage cannot say the priority without the pull request"),
                ("const without = String(tri.priority_without_pr || '').trim();\n    if (without && without !== C.priority) {",
                 "a failure that was P1 only for its open fix PR stays P1 once that PR has failed"),
                ("${status === 'ready_for_review' ? \"; once the run's own pull request is open, add its link to that comment\" : ''}",
                 "the user is told to link a pull request the run does not have"),
                ("const ver = await verifyLoop(extraGates, existing);\n  if (!ver.ok) return failed(ver, 'Verify');",
                 "a failure that is not the pull request's is decided against it"),
                # every check a trusted measurement failed, by its own name, and none that was not judged
                ("if (existing && perf.trusted && perf.failed.length) {", "a measurement that was not trusted is decided against the pull request, "
                                                                          "or a slower guard workload is not"),
                ("const judged = !!claim && !unjudged;", "a number that had no baseline to be judged against is said to have failed"),
                ("judged && HELD_OUT && p.held_out_ok !== true && 'the result does not hold at the held-out operating point',",
                 "a result that does not hold at the held-out point is said as a missed number, or left out next to one"),
                ("p.guards_ok !== true && 'on a guard workload it is slower than the baseline beyond the noise band',", "the run cannot tell a guard that failed"),
                ("return existingFailed(existing, 'Performance', `${perf.failed.join('; ')}${perf.summary ? ` (${perf.summary})` : ''}`);",
                 "the comment is written from a reason that names a check which did not fail"),
                ("const tried = await deliver([wp], gates, issueClaim, runPerf, prs || 'the existing fix PR');", "an issue's existing fix is not verified as someone else's"),
                ("if (tried !== EXISTING_FAILED) return tried;", "the run ends where its own fix should begin")):
            self.assertIn(said, src, why)
        issue = src[src.index("async function runIssue() {"):src.index("async function runCi() {")]
        self.assertLess(issue.index("if (tried !== EXISTING_FAILED) return tried;"), issue.index("enter('Diagnose');"),
                        "the run's own fix does not start with a diagnosis")
        # The repair of a fix of the run's own is unchanged: the check for someone else's comes first.
        self.assertLess(src.index("if (existing && inv.in_additions !== true\n"),
                        src.index("const fix = await implementAll(inv.work_packages, undefined, existing);"))
        # The comment is drafted before anything stops the run, and the branch is rolled back before a stop holds.
        fb = src[src.index("async function existingFailed(prs, at, why) {"):src.index("async function deliver(")]
        self.assertLess(fb.index("ROLLBACK, 'implementer:rollback:existing');"), fb.index("if (!sameCommit(target, base)) {"))
        self.assertLess(fb.index("if (!sameCommit(target, base)) {"), fb.index("R.done.length = 0;"),
                        "the pull request's record is cleared before the branch is known to be at the base")
        self.assertLess(fb.index("const cm = await call('pr_engineer', 'comment'"), fb.index("if (live.length) {"))
        self.assertLess(fb.index("if (live.length) {"), fb.index("ROLLBACK, 'implementer:rollback:existing');"))
        self.assertLess(fb.index("ROLLBACK, 'implementer:rollback:existing');"), fb.index("if (stopHere(at)) {"))

    def test_the_documents_say_it(self):
        role = (ROOT / "roles" / "pr_engineer.md").read_text(encoding="utf-8")
        self.assertIn("\n## PHASE=comment (issue: an existing fix PR that did not pass verification)\n", role)
        phase = " ".join(role[role.index("## PHASE=comment"):].split())
        for said in ("Its author is owed what the run saw, whatever becomes of the fix the run now makes itself",
                     "You change no code and run nothing: every number comes from the ledger",
                     "`RUN_DIR/pr_comment.md`", "the commit of it the run applied",
                     "what passed, since a comment that lists only failures misleads",
                     "no verdict on the pull request, no request to close it, and no attribution line",
                     # the comment is written before the run knows what becomes of its own fix
                     "Promise nothing about what follows", "it ends with what was measured, and names no pull request to come",
                     "its result tells the human to add that pull request's link when posting",
                     "Run the leak scan on it as on a PR body",
                     # the branch still carries the pull request: its findings are not the comment's
                     "rewrite the comment until the scan finds nothing in it",
                     "a finding there is not the comment's, and not yours to clear",
                     "name what the scan found on the branch in `branch_findings`, empty when it found nothing there",
                     '"branch_findings": "<what the leak scan found on the pull request\'s branch | \'\'>"',
                     '"comment_path": "/abs/RUN_DIR/pr_comment.md"'):
            self.assertIn(said, phase)
        for path, said in (
                ("knowledge/issue_triage.md", "**An existing fix that does not pass is answered, then replaced.**"),
                ("knowledge/issue_triage.md", "nothing is repaired on top of it"),
                ("knowledge/issue_triage.md", "goes on to Diagnose and a fix of its own, as for an issue nobody had opened a pull request for"),
                ("knowledge/issue_triage.md", "That fix's pull request names the earlier one and says in one sentence what it did not pass"),
                ("knowledge/issue_triage.md", "a measurement that could not be trusted decides nothing"),
                ("knowledge/issue_triage.md", "where it does not pass, the comment says so and the run fixes the failure itself |"),
                ("roles/investigator.md", "**After an existing fix PR that did not pass** (`existing_pr_result`"),
                ("roles/investigator.md", "plan from the root cause, not as a patch on that pull request"),
                ("roles/issue_triager.md", "With `existing_pr_result` an open fix PR was verified first and did not pass"),
                ("roles/issue_triager.md", "Say that the fix now offered is another one only for `ready_for_review`, where the run has one"),
                ("roles/issue_triager.md", "for any other ending say what the run reached after it (a diagnosis, a stop), and promise no fix"),
                ("roles/issue_triager.md", "return `priority_without_pr` as well, the priority it has when no usable fix PR exists"),
                ("roles/issue_triager.md", '"priority_without_pr": "<with push_existing_pr: P0 | P1 | P2, the priority with no usable fix PR>"'),
                ("roles/director.md", "With `existing_pr_result`, say that the run first verified that pull request"),
                ("roles/director.md", "only `ready_for_review` has a pull request to link from that comment, and the comment itself promises none"),
                # the run's own fix after it is prepared as any fix
                ("roles/pr_engineer.md", "With `existing_pr_result` the run first verified someone else's open fix, which did not pass"),
                ("roles/pr_engineer.md", "Step 5 writes a PR body for it, with the attribution line, and not a verification comment, whatever the "
                                         "triage record says of an existing PR"),
                # verified as it is
                ("roles/implementer.md", "`verify_only` (true when the package applies someone else's pull request that the run verifies as it is"),
                ("roles/implementer.md", "A cherry-pick that conflicts is not resolved: `git cherry-pick --abort`"),
                ("roles/implementer.md", "return `blocked` with the files that conflict in `conflicts`"),
                ("roles/implementer.md", "name the files only when git reported the conflict, never for another reason you could not go on"),
                ("roles/implementer.md", "the author's code and tests stay as they are, and where they fail, that is what the run reports"),
                ("roles/test_engineer.md", "`left_running` says what is left and where"),
                ("roles/test_engineer.md", "InferPilot then starts no fix round and no rollback next to it"),
                ("roles/test_engineer.md", '"left_running": ""'),
                # every answer says it; what the run added on top of the pull request is told apart from the pull request
                ("roles/test_engineer.md", "every answer gives it: one that leaves it out is taken for no answer, not for a gate that cleaned up"),
                ("roles/test_engineer.md", "nor the call that would finish the gate"),
                ("roles/test_engineer.md", "a gate that fails says in `failed_in` whose the failure is"),
                ("roles/test_engineer.md", "`additions`: only what the run added on top fails, a test the implementer wrote in a commit of its own "
                                           "after the author's"),
                ("roles/test_engineer.md", "Where both fail, it is `pr`"),
                ("roles/test_engineer.md", '"failed_in": "<verify_only, a failed gate: pr | additions>"'),
                ("roles/test_engineer.md", "Its `left_running` speaks for the gate as a whole: what the call before could not stop is named there "
                                           "too"),
                ("roles/test_engineer.md", "With `added_to` the package is the run's own, added on top of such a pull request"),
                ("roles/test_engineer.md", "`pr` there is a defect of the pull request that the addition shows"),
                ("roles/implementer.md", "`added_to` (the pull request under verification that this package is added on top of"),
                ("roles/implementer.md", "A test of the PR's that cannot fail (the test engineer finds that it passes without the change) is treated "
                                         "the same way: that is no failure of the fix, so add a test of your own that can fail"),
                ("roles/verify_engineer.md", "gives a round that asks for more time (`needs_min`) none while something is"),
                ("roles/verify_engineer.md", "Where a round it does not accept leaves the field out (one that failed, or a pass it refuses: one "
                                             "without the result the fix is accepted on), it calls PHASE=gpus once more before anything else "
                                             "works on these GPUs (`round: after <n>`)"),
                ("roles/verify_engineer.md", "A GPU idle before the round and in use after that stop counts as one the round still holds"),
                ("roles/verify_engineer.md", "With `round: after <n>` the call follows round `<n>`, which did not say whether it left anything "
                                             "running, and was not accepted or asked to be run again with more time"),
                ("roles/verify_engineer.md", "Such a call returns two fields more, in every answer. `left_running`: what you could not stop or "
                                             "could not find the log of, the empty string when all of it stopped"),
                ("roles/verify_engineer.md", "A driver or a suite that holds no GPU shows nowhere else"),
                ("roles/verify_engineer.md", "And `in_use_gpus`: the HIP number of every pinned GPU still in use, as a list (`[\"0\", \"1\"]`), "
                                             "empty when none is"),
                ("roles/verify_engineer.md", "InferPilot compares the numbers of the list, and reads no words for them"),
                ("roles/verify_engineer.md", 'Return for it: `{"in_use": "GPU 1: VRAM 12%, GPU 40%, pids 777 | \'\'", "in_use_gpus": ["1"], '
                                             '"left_running": ""}`'),
                ("roles/verify_engineer.md", "A round that needs more time and is not run again, for that or for what its own `left_running` "
                                             "names, is unfinished: InferPilot does not accept it, also where every failure it lists is one that "
                                             "does not block"),
                # what a PR phase exported from the branch that was rolled back does not stay next to the next export
                ("roles/implementer.md", "once the rollback of `ALL` is clean, move `RUN_DIR/patches`, `RUN_DIR/patches_additions` and "
                                         "`RUN_DIR/pr_body.md` to `RUN_DIR/failed/` where they exist"),
                ("roles/implementer.md", "`git am patches/*.patch` (README step 5) would replay the commits that were rolled back with the fix "
                                         "that replaces them"),
                ("roles/verify_engineer.md", "A round that asks for more time without the field is checked the same way before it is run again, "
                                             "and is not run again where the check could not stop something"),
                ("roles/investigator.md", "Say `in_additions` with whatever verdict you return for a failure that is the additions'"),
                ("knowledge/issue_triage.md", "The pull request stays on the branch until the comment for it is drafted, which reads the branch"),
                ("knowledge/issue_triage.md", "after a Verify round that is not accepted and does not say, the verify engineer stops what is left "
                                              "and checks the GPUs first, and nothing is investigated where it could not stop something, or where "
                                              "a GPU that was idle before the round is in use"),
                ("knowledge/issue_triage.md", "A measurement that fails after the pull request passed one is not the pull request's to answer for"),
                ("roles/verify_engineer.md", "`left_running` names one you cannot stop, and a GPU the round still holds, and is the empty string "
                                             "when everything stopped: say one or the other in every answer"),
                ("roles/verify_engineer.md", "After a round that failed, InferPilot starts no investigation, repair or rollback next to what is left"),
                ("roles/investigator.md", 'Add `"in_additions": true` when what fails is in what the run added on top of it'),
                ("roles/investigator.md", "your work packages then repair those additions and leave the author's commits alone"),
                ("roles/investigator.md", "Without it the failure is the pull request's: the run reports it there, and nothing is repaired"),
                ("roles/implementer.md", "`reset_to` is the commit the branch is at once you are done, as `git -C <worktree> rev-parse HEAD` gives "
                                         "it then, not the one you meant to reach"),
                ("roles/implementer.md", "it compares the two, and starts nothing on a branch that is elsewhere"),
                ("roles/director.md", "Return it (`base_commit`): a rollback of the whole task has to come back to that commit"),
                ("roles/director.md", '"base_commit": "<full sha>"'),
                ("roles/director.md", "With `branch_findings`, give what the leak scan found on that pull request's own branch"),
                ("knowledge/issue_triage.md", "What the run adds on top of the pull request is the run's own, and stays apart from it"),
                ("knowledge/issue_triage.md", "A failure there is repaired there (the unit gate says `failed_in`, the investigator `in_additions`), "
                                              "and is never held against the pull request"),
                ("knowledge/issue_triage.md", "a failure of the pull request found then is answered as one found the first time"),
                ("knowledge/issue_triage.md", "every round has to have said that it left nothing running (`left_running`, in every unit and Verify "
                                              "answer;"),
                ("knowledge/issue_triage.md", "the branch has to be at the task's base (`base_commit`, which Setup returns): the run's own fix "
                                              "starts on no other commit"),
                ("roles/investigator.md", 'Add `"attributed": true` when the controls show that the failure is the change\'s although you cannot explain it'),
                ("roles/investigator.md", "Leave it out where a control was interrupted, ended another way or was not run"),
                ("knowledge/issue_triage.md", "Verified as it is means that no conflict is resolved and no fix round repairs a test of its own that fails"),
                ("knowledge/issue_triage.md", "And only what is shown to be the pull request's counts against it"),
                ("knowledge/issue_triage.md", "A comparison that decided nothing, a package blocked for another reason and an answer that never came "
                                              "prove nothing"),
                ("knowledge/issue_triage.md", "Nothing is rolled back next to a call that may still be running"),
                ("knowledge/issue_triage.md", "A stop named for the phase the pull request failed in holds as well"),
                ("knowledge/issue_triage.md", "That fix has the priority the problem has without a usable fix PR"),
                ("knowledge/issue_triage.md", "The comment promises no fix: it is written before the run knows what becomes of its own"),
                ("knowledge/pr_playbook.md", "names that PR and says in one sentence what it did not pass"),
                ("templates/issue_triage.md", "the comment drafted for it (`pr_comment.md`)"),
                ("README.md", "| `pr_comment.md` |"), ("README.zh-CN.md", "| `pr_comment.md` |")):
            self.assertIn(said, self.flat(path), "%s does not say it" % path)


class LongContextScriptTest(unittest.TestCase):
    """G3 runs the task's own long-context test, `long_context_script`, as G4 keeps up the task's own client
    (knowledge/validation_doctrine.md): the user who asks for the gate says what it runs. A task with
    `long_context` on that names none is asked for it; InferPilot has no template for one, and no role writes
    one in its place. inferpilot.js, the roles, the templates and both READMEs say the same."""

    FIELD = "long_context_script"

    @staticmethod
    def flat(path):
        return " ".join((ROOT / path).read_text(encoding="utf-8").split())

    def test_the_task_definition_takes_the_script_and_every_role_reads_it(self):
        src = (ROOT / "inferpilot.js").read_text(encoding="utf-8")
        keys = re.search(r"const TASK_KEYS = \[(.*?)\];", src, re.S).group(1)
        intake = re.search(r"const TASK_DEF = S\(\{(.*?)\n\}\);", src, re.S).group(1)
        self.assertIn("'%s'" % self.FIELD, keys, "inferpilot.js does not take the script")
        self.assertIn("\n  %s: str," % self.FIELD, intake, "the intake cannot return the script")
        self.assertIn("\n  %s: String(A.%s || '').trim()," % (self.FIELD, self.FIELD), src, "the script does not reach the roles")
        self.assertIn("args.%s" % self.FIELD, src, "inferpilot.js's whenToUse does not name the script")
        fields = (ROOT / "templates" / "task_definition.md").read_text(encoding="utf-8")
        row = [line for line in fields.splitlines() if line.startswith("| `%s` |" % self.FIELD)]
        self.assertEqual(len(row), 1, "templates/task_definition.md has no row for the script")
        self.assertEqual(row[0].count("| with `long_context` on "), 4, "a feature, an issue, a ci and a perf task each name it with the gate on")
        self.assertIn("InferPilot has no template for it", row[0])
        gates = [line for line in fields.splitlines() if line.startswith("| `long_context`, `stability` |")]
        self.assertEqual(len(gates), 1)
        self.assertRegex(gates[0], r"\| optional \| optional \| optional \| optional \| \|$",
                         "the two gates are any task's that changes code to ask for, a ci task's too")
        for name, text in (("README.md", EN), ("README.zh-CN.md", ZH)):
            self.assertIn(self.FIELD, table_names(text), "%s has no row for the script" % name)

    def test_with_long_context_on_the_task_is_asked_for_its_test_and_nothing_stands_in(self):
        src = (ROOT / "inferpilot.js").read_text(encoding="utf-8")
        self.assertIn("const g3script = String(a.long_context_script || '').trim();\n"
                      "  if (mode !== 'scan' && asked(a.long_context) === 'true' && (!g3script || isTemplate(g3script))) {\n"
                      "    ask('long_context_script',", src, "long_context is on without a test for G3 to run: a path of blanks is none")
        question = re.search(r"\n    ask\('long_context_script',(.*?)\);\n", src, re.S)
        self.assertIsNotNone(question)
        self.assertNotIn("TEMPLATE", question.group(1), "the question offers a template InferPilot does not have")
        self.assertIn("InferPilot has no template for it", question.group(1))
        self.assertFalse((ROOT / "templates" / "harness" / "long_context.sh").exists(), "a template nobody asked for")

    def test_g3_runs_the_tasks_own_test_unchanged_on_both_arms(self):
        note = [i for i in items("knowledge/validation_doctrine.md") if i.startswith("- **G3 is the task's own long-context test.**")]
        self.assertEqual(len(note), 1, "knowledge/validation_doctrine.md does not say what G3 runs")
        for said in ("The user who asks for G3 says what it runs: the task's `long_context_script`",
                     "It runs unchanged on the baseline and on the change",
                     "A task with `long_context` on that names none is asked for it, in any mode",
                     "InferPilot has no template for a long-context test, and no role writes one in its place or changes the task's",
                     "G3 showed the server's long-context path, not the feature's"):
            self.assertIn(said, note[0])
        gate = [line for line in (ROOT / "knowledge" / "validation_doctrine.md").read_text(encoding="utf-8").splitlines()
                if line.startswith("| G3 long context |")]
        self.assertEqual(len(gate), 1)
        self.assertIn("the task's own long-context test (`long_context_script`)", gate[0])
        for path, said in (("roles/verify_engineer.md", "G3 long context is the task's own test, its `long_context_script`"),
                           ("roles/verify_engineer.md", "each run in a directory of its own that you make first: `mkdir -p "
                                                        "RUN_DIR/logs/g3_<arm>_c<n>_<run>`"),
                           ("roles/verify_engineer.md", "CONCURRENCY=<n> bash <long_context_script>"),
                           ("templates/task_definition.md", "a directory InferPilot makes for each run before it starts the script"),
                           ("roles/verify_engineer.md", "at two or more concurrencies, at least 4 runs each"),
                           ("roles/verify_engineer.md", "Never write a long-context test of your own, and never change the task's"),
                           ("roles/verify_engineer.md", "A script that does not read `CONCURRENCY` runs at its own"),
                           ("roles/verify_engineer.md", "G3 then showed the server's long-context path, not the feature's"),
                           ("roles/director.md", "and `long_context_script`, for which there is no template) run as given"),
                           ("roles/director.md", "G3, the task's `long_context_script`"),
                           ("roles/director.md", "G3's row names the task's script and the concurrencies it runs at"),
                           ("roles/intake.md", "`long_context_script` (`task_definition.md` §1.1): the user's own long-context test"),
                           ("roles/intake.md", "never answer `template`: InferPilot has no long-context test of its own"),
                           ("roles/intake.md", "With `long_context` on and no script, the run asks for it"),
                           ("knowledge/acceptance.md", "on the task's own test, its `long_context_script`"),
                           ("templates/task_definition.md", "**`long_context_script`**, with `long_context` on, runs the task's long-context "
                                                            "test against `PORT` and writes its results to `OUT_DIR`"),
                           ("templates/task_definition.md", "at least 4 times at each of two or more concurrencies"),
                           ("templates/task_definition.md", "It has no template for one, and writes none"),
                           ("templates/task_definition.md", "a task with `long_context` on names its `long_context_script`"),
                           ("templates/task_definition.md", "InferPilot has none for G3"),
                           ("templates/feature_spec.md", "the task's `long_context_script` at <two or more concurrencies>, at least 4 runs "
                                                         "each")):
            self.assertIn(said, self.flat(path), "%s does not say it" % path)
        for name, text, said in (("README.md", EN, "G3 runs it unchanged on the baseline and on the change"),
                                 ("README.zh-CN.md", ZH, "G3 在 baseline 和改动上原样运行它")):
            row = [line for line in text.splitlines() if line.startswith("| `%s` |" % self.FIELD)]
            self.assertEqual(len(row), 1, name)
            self.assertIn(said, row[0], "%s: the long_context_script row" % name)


class RecheckOnMainTest(unittest.TestCase):
    """A fix is verified against the commit its run began on, and main moves meanwhile. Before the pull request
    is prepared, the fix's gate runs again on the latest main without the fix (roles/verify_engineer.md,
    PHASE=recheck): a failure main no longer shows gets no pull request, and the run ends as fixed on main. A
    verified fix is dropped only on proof; a check that decides less prepares the pull request."""

    @staticmethod
    def flat(path):
        return " ".join((ROOT / path).read_text(encoding="utf-8").split())

    def test_the_run_checks_before_the_first_pr_round_and_drops_a_fix_only_on_proof(self):
        src = (ROOT / "inferpilot.js").read_text(encoding="utf-8")
        for said, why in (
                ("const RECHECK = S({ on_main: { type: 'string', enum: ['fails', 'passes', 'inconclusive'] }, main: str, main_import: str,",
                 "the check cannot say that it decided nothing"),
                ("test_path: str, fixed_by: str, evidence: str, left_running: str }, ['on_main', 'left_running']);",
                 "an answer that does not say what is left running is taken for one that says nothing is"),
                # only the test, the verify and the performance engineer produce numbers that count (guardrails.md)
                ("const rc = await call('verify_engineer', 'recheck',", "the check is run by a role whose numbers do not count"),
                ("inputs({ extra_gates: extraGates, claim: claim || '', time_limit_min: minutes(timeoutFor('verify_engineer', 'recheck')),\n"
                 "      env_record: envRecord }),", "the check is not given the fix's gate, the number to restore, its limit or where to record the image"),
                # the record of the image's packages is this invocation's, and the cleanup after the check is given the same one
                ("const RECHECK_ENV_ID = Date.now().toString(36);", "a retry of the task in another container restores the old image's packages"),
                ("const envRecord = `${C.run_dir}/recheck_env.${RECHECK_ENV_ID}.txt`;", "the record is not this invocation's"),
                ("RECHECK, label, ONE_SESSION);", "an answer that cannot be used is asked for again under what is left of the same limit"),
                ("const rc = await recheckOnMain(extraGates, runPerf === true ? perfClaim : '');", "main is not held to the number a performance problem must restore"),
                ("const granted = GRANTED_MS['issue_triager:reproduce'] || 0;", "a reproduction that was granted more time is run again with less"),
                ("const left = String(rc.left_running || '').trim();", "the pull request is prepared next to what the check could not stop"),
                ("const inTree = (p) => p.startsWith(`${C.run_dir}/recheck/`) && !/(^|\\/)\\.\\.(\\/|$)/.test(p);",
                 "a gate that ran the fixed tree passes for main"),
                ("ownTest && !inTree(test) &&", "a failing test collected from the task worktree passes for main's"),
                ("const lacks = [!sha && 'the commit of main it ran on', !evidence && 'the runs it rests on',", "a bare answer drops a verified fix"),
                ("C.recheck = R.recheck = {", "the report and the reply have only a note's 600 characters of the check"),
                ("if (TIMED_OUT_CALLS.has(label)) {", "the pull request is prepared next to a check that may still be running"),
                ("const gpus = await call('verify_engineer', 'gpus', inputs({ round: 'recheck', env_record: envRecord }), RECHECK_CLEANUP,\n"
                 "      'verify_engineer:gpus#recheck');", "what a check without an answer left on the GPUs is not stopped, or its record is not "
                                                           "the one the cleanup reads"),
                ("const GPU_CHECK = S({ in_use: str }, ['in_use']);", "an ordinary GPU check is asked about the image"),
                ("const RECHECK_CLEANUP = S({ in_use: str, env_changed: str }, ['in_use', 'env_changed']);",
                 "a cleanup that does not say whether the image changed is taken for a clean image"),
                ("const changed = String(gpus.env_changed || '').trim();\n    if (changed) {", "the pull request is prepared in an image the check left changed"),
                ("if (rc.fixed) return finish('fixed_on_main', 'PR');", "a fix main no longer needs still gets a pull request"),
                ("if (rc.stop) return finish('blocked', 'PR');", "the run goes on next to a check that may hold the GPUs"),
                ("if (PHASE !== 'PR') enter('PR');", "the PR phase is entered twice")):
            self.assertIn(said, src, why)
        self.assertLess(src.index("const rc = await recheckOnMain(extraGates, "), src.index("const pr = await preparePR(round);"),
                        "the check comes after the first pull request round")
        self.assertIn("only the test engineer, the verify engineer and the performance engineer produce numbers that count",
                      self.flat("knowledge/guardrails.md"))
        self.assertNotIn("PHASE=recheck", (ROOT / "roles" / "pr_engineer.md").read_text(encoding="utf-8"),
                         "the PR engineer, whose numbers do not count, still has the check")

    def test_the_documents_say_it(self):
        role = (ROOT / "roles" / "verify_engineer.md").read_text(encoding="utf-8")
        self.assertIn("\n## PHASE=recheck (issue, ci: before the pull request)\n", role)
        phase = " ".join(role[role.index("## PHASE=recheck"):].split())
        for said in ("A failure that main no longer shows needs no pull request", "changes nothing",
                     "so `passes` is said only on proof; anything short of it is `inconclusive`",
                     "a throwaway worktree of the latest main, without the fix",
                     "A `RUN_DIR/recheck` an earlier call left behind is removed first",
                     # the fetch is the framework's, not that of the directory the call runs in
                     "Then `git -C <framework_dir> fetch origin`",
                     "a bare fetch there leaves `origin/main` of the framework where it was",
                     # an earlier attempt may have left a server that reads the tree
                     "Before removing it, stop what that call launched",
                     "Stop every launch whose log is under `RUN_DIR/logs/recheck/`",
                     "Where you cannot confirm that nothing of it still runs, leave the tree as it is, run nothing, and answer `inconclusive` "
                     "with `left_running` naming what is left",
                     "The log of each launch goes in `RUN_DIR/logs/recheck/`",
                     # the image is the one the fix was verified in, before the check and after it
                     "First write the image's packages to `env_record`",
                     "an earlier attempt of this check wrote it in this invocation of the run",
                     "A `RUN_DIR/recheck_env.*.txt` under another name is an earlier invocation's",
                     "it describes another image, so leave it alone, and never restore from it",
                     "Put the change into effect for these runs in a directory of the check's own",
                     "Never in place: this call changes nothing, the pull request is checked in this image after it",
                     "Where main's change must replace a package of the image, or cannot be put into effect at all, answer `inconclusive`",
                     "Then compare the image's packages with `env_record`",
                     "one you cannot put back goes in `left_running` too",
                     # the gate was written for the task worktree, which has the fix
                     "Point the gate at that tree, and at nothing else",
                     "set `TREE` to the recheck worktree", "replace every path of the task worktree in the command itself",
                     "prints where the framework was imported from (sglang: `sglang.__file__`): return that path as `main_import`",
                     "For a failing test, return the file it was collected from as `test_path`",
                     "InferPilot counts `passes` only when both lie in `RUN_DIR/recheck`",
                     # main's own environment
                     "Main runs in the environment its own files call for",
                     "where main has changed a dependency declaration or the Dockerfile since `<base>`, that may be its fix",
                     # an intermittent failure
                     "as many times as the base needed to show the failure (the ledger has the counts), at least 6 for a failure that did "
                     "not show on every run there",
                     "one clean run after an intermittent failure is not evidence",
                     "keep a quarter of the time to it, at most 15 minutes, for step 6",
                     # a failure that main hides is not fixed
                     "The failing test was collected and ran, not skipped, deselected or marked as expected to fail",
                     "A number reaches `claim` and a score `accuracy_claim`, as the run established them",
                     "not a threshold main has lowered since",
                     "a test main now skips, or a threshold it has loosened, where the failure may be hidden and not fixed",
                     "A run that fails another way (an import error, a server that stops for another reason)",
                     # what the reply is checked against, and what is left behind
                     "Write the same in the ledger (`RUN_DIR/experiment_ledger.md`)",
                     "keep each run's log under `RUN_DIR/logs/`, not in the worktree",
                     "What you could not stop goes in `left_running`, whatever the answer, and an empty string when all of it stopped",
                     "Remove the worktree once everything you started has stopped",
                     '"on_main": "fails | passes | inconclusive", "main": "<sha of the main it ran on>"',
                     '"left_running": ""'):
            self.assertIn(said, phase)
        for path, said in (("knowledge/pr_playbook.md", "A fix (issue, ci) is checked against main once more before its PR is prepared"),
                           ("knowledge/pr_playbook.md", "no PR is prepared, and the run ends as fixed on main"),
                           ("knowledge/pr_playbook.md", "A verified fix is dropped only on proof"),
                           ("knowledge/pr_playbook.md", "A check that decides less than that prepares the PR"),
                           ("roles/director.md", "or the check on the latest main before the pull request"),
                           # the report names the fix whichever phase named it
                           ("roles/director.md", "and name the fix (`fixed_by`), whichever phase named it: Classify for a CI job whose tests pass "
                                                 "on main, Bisect for an issue, or the check on the latest main before the pull request"),
                           # bisect_fixed is an issue's: a ci task is given the range, and no run that does not exist
                           ("roles/director.md", "Where none names it, say that the change that fixed it is not named, and give the range it "
                                                 "lies in"),
                           ("roles/director.md", "For an issue, give the `bisect_fixed` run that would name it; a ci task has no such run, so offer "
                                                 "none"),
                           ("roles/verify_engineer.md", "With `round: recheck`, look at the image's environment as well, once the launches have "
                                                        "stopped"),
                           ("roles/verify_engineer.md", "`env_changed` names what you could not put back"),
                           ("roles/verify_engineer.md", "`env_record` is this invocation's record, and the only one that counts"),
                           ("roles/verify_engineer.md", "Never compare with that one, and never restore from it"),
                           ("roles/verify_engineer.md", "Give it in every answer to this round, the empty string included: an answer without it is "
                                                        "taken for no answer, and the run stops"),
                           ("roles/director.md", "say that its branch was not proposed, and where it is"),
                           ("roles/issue_triager.md", "main fixed it while the run worked on a fix of its own, which is not offered"),
                           ("roles/verify_engineer.md", "`recheck` when it follows a check on the latest main that left no answer"),
                           ("README.md", "before the PR of a fix, runs its gate again on the latest main without the fix"),
                           ("README.zh-CN.md", "修复提 PR 之前，在最新的 main 上不带修复重跑一次它的 gate")):
            self.assertIn(said, self.flat(path), "%s does not say it" % path)


class LongGatesTest(unittest.TestCase):
    """G3 and G4 run only when the task sets `long_context` or `stability`, two fields inferpilot.js takes
    and hands to every role. G4 is the task's performance client run back to back for more than 48 hours
    (scripts/stability.sh), once in a run, judged between its first hours and its last; its failure gets
    its baseline control in the same round, is the baseline's only at a comparable time, and is never
    reported as a pass (knowledge/validation_doctrine.md). The roles, the templates and both READMEs say
    the same."""

    FLAGS = ("long_context", "stability")

    @staticmethod
    def flat(path):
        return " ".join((ROOT / path).read_text(encoding="utf-8").split())

    def test_the_task_definition_takes_the_two_fields_off_by_default(self):
        src = (ROOT / "inferpilot.js").read_text(encoding="utf-8")
        keys = re.search(r"const TASK_KEYS = \[(.*?)\];", src, re.S).group(1)
        intake = re.search(r"const TASK_DEF = S\(\{(.*?)\n\}\);", src, re.S).group(1)
        fields = (ROOT / "templates" / "task_definition.md").read_text(encoding="utf-8")
        row = [line for line in fields.splitlines() if line.startswith("| `long_context`, `stability` |")]
        self.assertEqual(len(row), 1, "templates/task_definition.md has no row for the two fields")
        self.assertIn("each off by default", row[0])
        for flag in self.FLAGS:
            self.assertIn("'%s'" % flag, keys, "inferpilot.js does not take %s" % flag)
            self.assertIn("\n  %s: { enum: [true, false, 'true', 'false', ''] }," % flag, intake,
                          "the intake's %s is not true or false: another word would be read as off" % flag)
            self.assertRegex(src, r"\nconst [A-Z_]+ = on\(A\.%s\);" % flag, "%s is not off unless the task sets it" % flag)
            self.assertRegex(src, r"\n  %s: [A-Z_]+,\n" % flag, "%s does not reach the roles" % flag)
            self.assertIn("args.%s" % flag, src, "inferpilot.js's whenToUse does not name %s" % flag)
            for name, text in (("README.md", EN), ("README.zh-CN.md", ZH)):
                self.assertIn(flag, table_names(text), "%s has no row for %s" % (name, flag))

    def test_g3_and_g4_run_only_when_the_task_sets_their_field(self):
        for path in ("knowledge/validation_doctrine.md", "knowledge/acceptance.md", "knowledge/dev_methodology.md",
                     "roles/intake.md", "roles/director.md", "roles/verify_engineer.md",
                     "templates/task_definition.md", "templates/feature_spec.md"):
            text = self.flat(path)
            for flag in self.FLAGS:
                self.assertIn("`%s`" % flag, text, "%s does not name %s" % (path, flag))
            self.assertNotRegex(text, r"G3 and G4[^.|]*`goal`[^.|]* asks", "%s still makes `goal` the switch" % path)
        self.assertIn("G3 only with `long_context` on, G4 only with `stability` on", self.flat("roles/verify_engineer.md"))
        self.assertIn("`goal` does not turn a gate on", self.flat("knowledge/validation_doctrine.md"))
        self.assertIn("a `goal` the caller gave does not drop what the request asked", self.flat("roles/intake.md"))
        spec = (ROOT / "templates" / "feature_spec.md").read_text(encoding="utf-8")
        for gate, flag in zip(("G3", "G4"), self.FLAGS):
            row = [line for line in spec.splitlines() if line.startswith("| %s " % gate)]
            self.assertEqual(len(row), 1, gate)
            self.assertIn("only with `%s` on; else `not run: not asked`" % flag, row[0], gate)
        acceptance = (ROOT / "knowledge" / "acceptance.md").read_text(encoding="utf-8")
        profile = [line for line in acceptance.splitlines() if line.startswith("| Stability | ") and "G4)" in line]
        self.assertEqual(len(profile), 1)
        self.assertNotIn("required", profile[0], "no profile makes G4 required")

    def test_g4_is_one_run_of_more_than_48_hours(self):
        for path in ("knowledge/validation_doctrine.md", "knowledge/acceptance.md", "roles/verify_engineer.md",
                     "roles/director.md", "roles/intake.md", "templates/task_definition.md", "templates/feature_spec.md"):
            self.assertRegex(self.flat(path), r"more than 48 h(?:ours)?\b", "%s does not give G4 its 48 hours" % path)
        verify = self.flat("roles/verify_engineer.md")
        self.assertNotIn("G3/G4 at two or more concurrencies", verify, "G4 is one run, at one concurrency")
        self.assertIn("G4 stability is one run, not repeated and not taken to other concurrencies", verify)
        doctrine = self.flat("knowledge/validation_doctrine.md")
        self.assertNotIn("Run G3/G4 at more than one concurrency", doctrine)
        self.assertIn("G4 is the exception: one run of more than 48 hours", doctrine)

    def test_a_drift_is_judged_between_the_first_hours_and_the_last(self):
        """Not by the spread of the whole run: a steady fall from 100 to 50 lies within its own range."""
        hourly = ("Each hour of the run has one value of throughput and one of memory: the median of the passes "
                  "that ended in it, the first pass left out as warm-up.")
        window = ("The median of the last 6 hours' values is no lower in throughput, and no higher in memory, than "
                  "the median of the first 6 hours' by more than the range of those first 6 values.")
        for path in ("knowledge/validation_doctrine.md", "roles/verify_engineer.md"):
            text = self.flat(path)
            self.assertIn(hourly, text, "%s does not say what an hour's value is" % path)
            self.assertIn(window, text, "%s does not compare the first hours with the last" % path)
            self.assertNotRegex(text, r"beyond the spread of (?:its hourly|the) samples", path)

    def test_a_failed_g4_gets_its_control_in_the_round_and_stays_failed(self):
        verify = self.flat("roles/verify_engineer.md")
        self.assertIn("A failed G4 gets its control in this round", verify)
        self.assertIn("its estimate counts G4 twice, its run and the control a failure would need", verify)
        self.assertIn("A known issue never turns a failed G4 into a pass** (step 4 does not apply to it)", verify)
        self.assertIn("until `known_issues` holds it as the baseline's: from then on report it as failed, with that "
                      "issue, in `failures` with `blocking: false`", verify)
        investigator = self.flat("roles/investigator.md")
        self.assertIn("comes with its same-pattern baseline control already run", investigator)
        doctrine = self.flat("knowledge/validation_doctrine.md")
        self.assertIn("A failed G4 gets its baseline control in the same round, and stays failed", doctrine)
        self.assertIn("G4 keeps its verdict even so", doctrine)

    def test_a_g4_failure_is_the_baselines_only_at_a_comparable_time(self):
        """The same signature is not enough: hour 1 against the baseline's hour 47 is a change in rate."""
        for path in ("knowledge/validation_doctrine.md", "roles/investigator.md"):
            text = self.flat(path)
            self.assertIn("the same failure at a comparable time", text, path)
            self.assertRegex(text, r"Hour 31 against the baseline's hour 27 is(?: comparable)?; hour 1 against hour 47 is a "
                                   r"change in rate, which is the change's", path)
            self.assertRegex(text, r"A control that ended another way first, or was interrupted, (?:says|shows) nothing "
                                   r"about this failure", path)
            self.assertIn("`inconclusive`", text, path)

    def test_the_investigator_starts_no_control_it_cannot_finish(self):
        investigator = self.flat("roles/investigator.md")
        self.assertIn("never start a run that cannot finish in it", investigator)
        self.assertIn("return `inconclusive` with the long controls still to run", investigator)

    def test_g4_runs_once_in_a_run_and_its_result_names_its_commit(self):
        self.assertIn("G4 runs once in a run, and its result names its commit", self.flat("knowledge/validation_doctrine.md"))
        verify = self.flat("roles/verify_engineer.md")
        self.assertIn("**G4 runs once in a run.** Write the commit it ran on", verify)
        self.assertIn("say in `gates` that this commit has not been through G4", verify)
        self.assertIn("Only a G4 whose failure was the change's is run again, on the fix", verify)
        self.assertIn("the code handed over has not been through G4", self.flat("roles/director.md"))
        self.assertIn("G4 runs once in a run", self.flat("templates/task_definition.md"))
        self.assertIn("once in a run", EN)
        self.assertIn("一次运行里只跑一次", ZH)

    def test_g4s_load_is_the_runs_performance_client_and_the_task_brings_one(self):
        doctrine = self.flat("knowledge/validation_doctrine.md")
        self.assertIn("G4 is the task's performance test, run back to back for more than 48 hours", doctrine)
        self.assertNotIn("throughput_test.py", doctrine, "a finite batch looped lets the concurrency drain")
        for path in ("knowledge/validation_doctrine.md", "roles/verify_engineer.md", "templates/task_definition.md",
                     "templates/feature_spec.md", "README.md", "README.zh-CN.md"):
            self.assertIn("scripts/stability.sh", self.flat(path), "%s does not name G4's script" % path)
        self.assertTrue((ROOT / "scripts" / "stability.sh").is_file())
        src = (ROOT / "inferpilot.js").read_text(encoding="utf-8")
        self.assertIn("const g4 = (mode === 'issue' || mode === 'ci') && asked(a.stability) === 'true';", src,
                      "a feature and a perf task name their server and their client anyway: G4 asks an issue or a ci task")
        self.assertIn("if ((g4 || fixes) && !a.client_script) {", src, "stability is on without a client to run")
        for field in self.FLAGS:
            self.assertIn("['%s', '" % field, src, "a value of %s that is neither true nor false is not asked about" % field)

    def test_with_stability_on_the_task_names_its_server_and_its_client_and_no_template_is_offered(self):
        """The user asks for G4 and says what it keeps up for two days: the server and the client are the task's,
        asked for where it lacks one, in any mode. No template stands in on G4's account, and InferPilot's client,
        where the task names it all the same, runs the task's workload, never lengths and a concurrency of its own."""
        src = (ROOT / "inferpilot.js").read_text(encoding="utf-8")
        self.assertIn("if (g4 && !a.server_script) {", src,
                      "an issue or a ci task with stability on may start without a server for G4")
        self.assertIn("if ((g4 || fixes) && gaps.length) {", src, "the client template would keep up its own lengths and concurrency")
        block = re.search(r"\n  const g4 = .*?\n  const kb = ", src, re.S)
        self.assertIsNotNone(block, "the G4 questions are not where missingFields has them")
        self.assertEqual(block.group(0).count("ask('"), 3, "G4 asks for its server, its client and the template's workload")
        self.assertNotIn("[TEMPLATE]", block.group(0), "a G4 question offers the template")
        self.assertNotIn("Or ${TEMPLATE}", block.group(0), "a G4 question offers the template")
        self.assertNotIn("A.client_script = TEMPLATE", src, "the run takes the template for G4 without being told to")
        for path, said in (("templates/task_definition.md", "names its `server_script` and its `client_script`, whatever its mode"),
                           ("templates/task_definition.md", "No template is offered for either on G4's account"),
                           ("templates/task_definition.md", "gives the `workload` it runs, and is asked for that"),
                           ("knowledge/validation_doctrine.md", "The server, the client and the workload are the task's to bring"),
                           ("knowledge/validation_doctrine.md", "no template is taken for it"),
                           ("knowledge/validation_doctrine.md", "never on lengths and a concurrency of its own"),
                           ("roles/intake.md", "a task without a `server_script` or without a `client_script` is asked for it, in any mode"),
                           ("roles/intake.md", "An issue's or a ci task's question offers no `template`"),
                           ("README.md", "names its `server_script` and its `client_script`, in any mode, and is asked for the one it lacks: "
                                         "G4 takes no template of its own accord"),
                           ("README.zh-CN.md", "任何模式下任务都要给出自己的 `server_script` 和 `client_script`，缺哪个就问哪个：G4 不会自行改用模板")):
            self.assertIn(said, self.flat(path), "%s does not say it" % path)
        fields = (ROOT / "templates" / "task_definition.md").read_text(encoding="utf-8")
        server = [line for line in fields.splitlines() if line.startswith("| `server_script` |")]
        self.assertEqual(len(server), 1)
        self.assertIn("| recommended; required with `stability` on |", server[0], "an issue with stability on needs its server script")
        both = [line for line in fields.splitlines() if line.startswith("| `long_context`, `stability` |")]
        self.assertIn("so the task names both", both[0])


class ComponentCauseTest(unittest.TestCase):
    """A root cause in an AMD component outside the framework (aiter, MoRI, ROCm, the ROCm build of PyTorch) is
    that component's to fix: the issue for its repository is drafted, and the framework gets a workaround only
    for a P0 issue (knowledge/issue_triage.md §5). A CI failure is fixed as any other, with the draft besides."""

    @staticmethod
    def flat(path):
        return " ".join((ROOT / path).read_text(encoding="utf-8").split())

    def test_the_run_decides_by_the_priority(self):
        src = (ROOT / "inferpilot.js").read_text(encoding="utf-8")
        for said in ("function componentCause(found, said) {",
                     "C.component_issue = R.component_issue = { component, draft_path: draft, summary: String(said || '').trim().slice(0, 1200) };",
                     "if (C.component_issue && C.priority !== 'P0') {",
                     "const noDraft = ['fix_needed', 'baseline_bug'].includes(dg.verdict) ? componentCause(dg, dg.summary) : '';",
                     "file the issue draft in ${R.component_issue.component}'s repository"):
            self.assertIn(said, src)
        self.assertIn("const COMPONENTS = ['aiter', 'mori', 'rocm', 'pytorch_rocm'];", src)
        self.assertIn("const COMPONENT_CAUSE = { component: { type: 'string', enum: ['', ...COMPONENTS] }, component_issue_path: str };", src,
                      'any word is taken for a component: "none" would stop a fix of the framework\'s own')
        self.assertIn("if (!COMPONENTS.includes(component)) return '';", src)
        # ci: the draft is recorded before the check that ends a run without a fix to make.
        self.assertLess(src.index("const noDraft = bi && bi.confirmed === true ? componentCause(bi, bi.evidence) : '';"),
                        src.index("if (!bi || bi.confirmed !== true || !(bi.work_packages || []).length) {"),
                        "a defect the lane cannot be fixed for loses its draft")
        for schema in ("DIAG", "BISECT"):
            self.assertIn("Object.assign(%s.properties, COMPONENT_CAUSE);" % schema, src, "%s takes no component" % schema)

    def test_the_rule_is_written_where_each_role_reads_it(self):
        for path, said in (
                ("knowledge/issue_triage.md", "**A root cause in an AMD component outside the framework** (aiter, MoRI, ROCm, the ROCm build of PyTorch) is that component's to fix."),
                ("knowledge/issue_triage.md", "**P0:** a workaround in the framework, HIP-only"),
                ("knowledge/issue_triage.md", "so G2 and G5 measure it"),
                ("knowledge/issue_triage.md", "**P1 and P2:** no workaround. The run ends at Diagnose (`diagnosed`)"),
                ("knowledge/issue_triage.md", "A component that changed its interface on purpose has no defect"),
                ("roles/investigator.md", "and draft the issue for its repository as `RUN_DIR/component_issue.md` (`component_issue_path`)"),
                ("roles/investigator.md", "for a P0, `work_packages` are the workaround in the framework"),
                ("roles/investigator.md", "for any other issue return none."),
                ("roles/investigator.md", "Never change the component: it comes from the image"),
                ("roles/investigator.md", "Return `component` (`aiter`, `mori`, `rocm` or `pytorch_rocm`, and nothing for any other cause"),
                ("roles/investigator.md", "Either way `summary` says what the reporter can do meanwhile"),
                ("roles/issue_triager.md", "as the diagnosis gave it (`component_issue.summary`)"),
                ("roles/ci_guardian.md", "where you can plan none, return the component and its draft all the same"),
                ("roles/issue_triager.md", "With `component_issue` the root cause is in an AMD component outside the framework"),
                ("roles/issue_triager.md", "`<component issue link>` placeholder, since the human files that issue"),
                ("roles/ci_guardian.md", "and draft the issue for its repository as `RUN_DIR/component_issue.md` (`component_issue_path`)"),
                ("roles/ci_guardian.md", "The lane is still fixed by the package; where you can plan none"),
                ("knowledge/ci_triage.md", "**A defect of an AMD component** outside the framework"),
                ("knowledge/ci_triage.md", "with a pin to the last version that passed where a bump brought it"),
                ("roles/director.md", "With `component_issue`, name the component the root cause is in"),
                ("templates/issue_triage.md", "the issue drafted for its repository (`component_issue.md`)"),
                ("templates/ci_triage.md", "| defect of an AMD component |"),
                ("README.md", "| `component_issue.md` |"),
                ("README.zh-CN.md", "| `component_issue.md` |")):
            self.assertIn(said, self.flat(path), "%s does not say it" % path)

    def test_the_components_are_the_ones_the_priority_rubric_calls_the_amd_side(self):
        # issue_triage.md §2 names the AMD-maintained stacks; the rule is about the same four, wherever it is written.
        rubric = self.flat("knowledge/issue_triage.md")
        self.assertIn("AMD-maintained stacks (aiter, MoRI, ROCm, the ROCm build of PyTorch)", rubric)
        for path in ("knowledge/issue_triage.md", "knowledge/ci_triage.md", "roles/investigator.md", "roles/ci_guardian.md"):
            self.assertIn("(aiter, MoRI, ROCm, the ROCm build of PyTorch", self.flat(path), path)


class FlagsTest(unittest.TestCase):
    def test_bootstrap_flags_the_docs_give_exist_and_every_one_is_documented(self):
        defined = argparse_flags(ROOT / "inferpilot" / "bootstrap.py")
        self.assertTrue(defined)
        for name, text in (("README.md", EN), ("README.zh-CN.md", ZH), ("AGENTS.md", (ROOT / "AGENTS.md").read_text(encoding="utf-8"))):
            for flag in sorted(flags_in(text, "inferpilot.bootstrap") - defined):
                self.fail("%s gives inferpilot.bootstrap %s, which it does not take" % (name, flag))
        for flag in sorted(defined):
            for name, text in (("README.md", EN), ("README.zh-CN.md", ZH)):
                self.assertTrue(mentions(flag, text), "%s does not mention inferpilot.bootstrap %s" % (name, flag))

    def test_runner_flags_read_listed_and_given_are_the_same(self):
        run = ROOT / "runtime" / "run.mjs"
        src = run.read_text(encoding="utf-8")
        read = {"--" + (a or b) for a, b in re.findall(r"opts(?:\.([a-z]+)\b|\['([a-z-]+)'\])", src)}
        taken = {"--" + f for f in re.findall(r"'([a-z-]+)'", re.search(r"const FLAGS = \[(.*?)\]", src).group(1))}
        usage = set(re.findall(r"--[a-z][a-z0-9-]*", re.search(r"usage: node runtime/run\.mjs(.*?)\\n'", src, re.S).group(1)))
        comment = {a or b for a, b in re.findall(r"^//\s+(--[a-z][a-z0-9-]*)|\| (--[a-z][a-z0-9-]*)", header(run), re.M)}
        self.assertEqual(read, taken, "run.mjs reads these flags; it takes those (any other stops it)")
        self.assertEqual(read, usage, "run.mjs reads these flags; its usage line lists those")
        self.assertEqual(read, comment, "run.mjs reads these flags; the comment it opens with lists those")
        for name in ("README.md", "README.zh-CN.md", "AGENTS.md"):
            given = flags_in((ROOT / name).read_text(encoding="utf-8"), "runtime/run.mjs")
            self.assertLessEqual(given, read, "%s gives runtime/run.mjs flags it does not take" % name)


def items(path):
    """A document's paragraphs, list items and table rows, each on one line."""
    text = (ROOT / path).read_text(encoding="utf-8")
    return [" ".join(part.split()) for part in re.split(r"\n(?=[ \t]*(?:\d+\.|[-*]) |\|)|\n[ \t]*\n", text)]


class FeatureOffTest(unittest.TestCase):
    """G0 turns a feature off, so it runs only where the change has a switch (knowledge/validation_doctrine.md).
    Every mode that changes code still answers for it: the roles that report it say what they report for a
    change without a switch, and the documents of the modes that fix things ask for it only where there is one.
    Such a change is judged against the tree without it: a test by how it fails there, not by an absolute pass;
    a smoke only where the change is meant to keep what the server does; the review lets no regression through
    as minor; and the investigator's feature-off control becomes the change reverted."""

    @staticmethod
    def flat(path):
        return " ".join((ROOT / path).read_text(encoding="utf-8").split())

    def test_the_doctrine_gives_g0_only_to_a_change_with_a_switch(self):
        note = [i for i in items("knowledge/validation_doctrine.md") if i.startswith("- **G0 needs a switch.**")]
        self.assertEqual(len(note), 1, "knowledge/validation_doctrine.md does not say where G0 runs")
        for said in ("without a switch", "has no G0", "`roles/test_engineer.md`", "`roles/pr_engineer.md`",
                     "`roles/reviewer.md`"):
            self.assertIn(said, note[0])

    def test_every_role_that_reports_g0_says_what_it_reports_without_a_switch(self):
        for path, result in (("roles/test_engineer.md", "`feature_off_unchanged`"),
                             ("roles/pr_engineer.md", "`feature_off_final`"), ("roles/verify_engineer.md", "`gates`")):
            named = [i for i in items(path) if re.search(r"\bG0\b", i) and "feature off" in i.lower()]
            self.assertEqual(len(named), 1, "%s: the steps that run G0" % path)
            self.assertRegex(named[0], r"without (?:a switch|one)", "%s runs G0 on a change with nothing to turn off" % path)
            self.assertIn(result, named[0], "%s does not say what %s reports without a switch" % (path, result))

    def test_a_fix_is_asked_for_g0_only_behind_a_features_switch(self):
        for path in ("knowledge/ci_triage.md", "knowledge/issue_triage.md"):
            named = [i for i in items(path) if re.search(r"\bG0\b", i)]
            self.assertTrue(named, "%s does not say when a fix needs G0" % path)
            for item in named:
                self.assertIn("only when the fix is behind a feature's switch", item, path)
                self.assertIn("existing tests of the module", item, "%s: what a fix without a switch shows instead" % path)

    def test_without_a_switch_a_test_is_judged_by_how_it_fails_without_the_change(self):
        """Not by a pass on the change alone, which a test the base already fails never gives; not by pass or
        fail alone, which takes an assertion that became a crash for no change; and a test the fix made
        runnable has no result on the parent to be compared with."""
        self.assertIn("judged against the tree without it, never as an absolute pass",
                      self.flat("knowledge/validation_doctrine.md"))
        for path, same in (("knowledge/validation_doctrine.md", "fails there the same way: the same test, the same error"),
                           ("roles/test_engineer.md", "fails there the same way, the same test with the same error"),
                           ("roles/pr_engineer.md", "fails the same way on the base, the same test with the same error"),
                           ("knowledge/pr_playbook.md", "or fail the same way on the base")):
            self.assertIn(same, self.flat(path), "%s does not say which failure the change did not cause" % path)
        for path in ("knowledge/validation_doctrine.md", "roles/test_engineer.md", "roles/pr_engineer.md"):
            text = self.flat(path)
            self.assertIn("fails there another way", text, "%s takes a failure that changed for no change" % path)
            self.assertIn("cannot run at all because of the failure", text,
                          "%s: a test the fix made runnable has no result without the fix" % path)
            self.assertIn("the last commit that could run it, the culprit's parent for a CI fix", text, path)
        engineer, pr = self.flat("roles/test_engineer.md"), self.flat("roles/pr_engineer.md")
        self.assertIn("each one that fails again on the parent", engineer, "nothing asks for the parent's result")
        self.assertNotIn("no test which passes on the parent fails", engineer)
        self.assertIn("each one that fails again on the base", pr, "nothing asks for the base's result")
        self.assertIn("`feature_off_final` is then true when", pr, "the PR engineer is not told when the check passes")

    def test_without_a_switch_the_smoke_is_compared_only_where_the_change_keeps_what_the_server_does(self):
        """The baseline of the case a fix is for is the failure: a smoke that had to match it would have a
        correct fix rolled back at Implement, before the mode's gate could judge it."""
        doctrine, engineer = self.flat("knowledge/validation_doctrine.md"), self.flat("roles/test_engineer.md")
        self.assertIn("A server smoke is compared only with a baseline of a case the change is meant to keep", doctrine)
        self.assertIn("**The smoke,** for a package on the serving path, only against a baseline of a case the package "
                      "is meant to keep", engineer)
        for name, text in (("the doctrine", doctrine), ("the test engineer", engineer)):
            self.assertIn("meant to behave differently with the fix", text, name)
            self.assertRegex(text, r"the mode's gate[^.]* judge that difference", name)
        self.assertNotIn("matches its baseline", engineer)

    def test_the_review_lets_no_regression_outside_the_changes_case_through_as_minor(self):
        harm = [i for i in items("roles/reviewer.md") if "**Do-no-harm.**" in i]
        self.assertEqual(len(harm), 1)
        self.assertIn("is a `major` finding, whose work package narrows the change to its case", harm[0])
        self.assertIn("only when the change cannot avoid it and the evidence shows that case is no worse for it", harm[0])
        self.assertIn("the verdict is `blocked`", harm[0], "a regression the change cannot avoid is a human's to accept")
        self.assertNotIn("one the change cannot avoid is a `minor`", harm[0])

    def test_the_control_that_turns_the_feature_off_has_a_form_without_a_switch(self):
        for path in ("roles/investigator.md", "knowledge/debugging_playbook.md", "templates/experiment_ledger.md"):
            named = [i for i in items(path) if re.search(r"feature off on (?:the )?branch", i, re.I)]
            self.assertTrue(named, "%s has no feature-off control" % path)
            for item in named:
                self.assertIn("without a switch", item, "%s asks a change with nothing to turn off for this control" % path)
                self.assertIn("the change's commits reverted", item, path)
        again = [i for i in items("knowledge/debugging_playbook.md") if re.search(r"\bG0\b", i)]
        self.assertTrue(again)
        for item in again:
            self.assertIn("without a switch", item, "the playbook runs G0 again after a fix, switch or no switch")


class FixGatesByFileTest(unittest.TestCase):
    """The diff of a fix is read once, for G2 and for G5, by one line drawn by file
    (knowledge/validation_doctrine.md, What a fix changes decides its gates): both gates run for every fix that
    changes a file other than tests, documentation and CI files. Of any other file nobody judges whether the
    server runs it, since that judgement can be wrong: a fix to a Dockerfile, a tool or a dependency file is
    measured like one to a model file, and a new version of a runtime dependency with that version installed."""

    @staticmethod
    def flat(path):
        return " ".join((ROOT / path).read_text(encoding="utf-8").split())

    def test_the_doctrine_draws_one_line_for_both_gates(self):
        note = [i for i in items("knowledge/validation_doctrine.md") if i.startswith("- **What a fix changes decides its gates.**")]
        self.assertEqual(len(note), 1, "knowledge/validation_doctrine.md does not say what a fix's files decide")
        for said in ("read once, for G2 and for G5, by one line drawn by file",
                     "both gates run for every fix that changes a file other than tests, documentation and CI files "
                     "(sglang: `test/`; `docs/`, `*.md`; `.github/`, `scripts/ci/`)",
                     "That is what *code the server runs* means wherever a rule on a fix names it",
                     "Whether the server in fact runs such a file is nobody's to judge",
                     "an accuracy or a speed loss let through on such a reading is the costly mistake",
                     "a fix to a Dockerfile, a benchmark or tool script or a dependency file is measured like a fix to a model file, "
                     "on gsm8k and on the task's client",
                     "A change outside the framework's code takes effect only once it is put into effect",
                     "an environment variable it sets, such as `SGLANG_USE_AITER`",
                     "So every tree runs in the environment its own files call for: the fix with the change in effect, the base with the "
                     "image's, a parent or a mutant of a commit with what that tree declares",
                     "the unit test and its control at Implement, the feature-off checks, `issue_repro` or `failing_test`, both arms of G2 "
                     "and of G5, and the command the pull request gives",
                     "The same run in the unchanged environment shows nothing",
                     "a new version of a package the server runs (aiter, PyTorch, Triton, flashinfer), bumped or pinned",
                     # the arm's own environment first: nothing to restore, nothing left behind by a call that times out
                     "The environment is the arm's own wherever it can be",
                     "`pip install --no-deps --target RUN_DIR/env/<arm>`",
                     "a call that times out leaves nothing behind",
                     "Only a package that must replace the image's is installed in place",
                     "The image's state and the commands that restore it are written first (`RUN_DIR/env_restore.sh`, the ledger)",
                     "Every later launch, in this call or any other, checks the environment against that record before it starts",
                     "The arms run one after the other, never side by side",
                     "put back once every process of the round has stopped, not before",
                     "The report's commands carry the setup",
                     "Install what the change names and nothing else: never the framework again",
                     "the gate is a blocking failure of the class `environment`",
                     "It is not investigated, and no fix is planned for it",
                     "Only what this run's platform uses counts",
                     "`python/pyproject_other.toml`, `python/pyproject_rocm.toml`, `docker/rocm.Dockerfile`",
                     "a version moved only in the CUDA declaration changes nothing in this run",
                     "cannot be installed in this image"):
            self.assertIn(said, note[0])
        # A change that is not in effect is not measured as if it were: the note no longer lets a gate run both arms
        # in one environment and say so.
        self.assertNotIn("the change was not in effect", note[0])
        # No second, narrower line for G5: what the server is judged not to run is measured all the same.
        for gone in ("G5 runs when the fix changes code the server runs", "has no G5: `not run", "each by a line of its own"):
            self.assertNotIn(gone, note[0], "G5 still has a line of its own")
        for gate in ("G2", "G5"):
            text = [i for i in items("knowledge/validation_doctrine.md") if i.startswith("- **%s for a fix.**" % gate)][0]
            self.assertIn("anything but tests, documentation and CI files", text, "%s for a fix does not draw the line by file" % gate)
            self.assertNotIn("when it changes code the server runs", text, "%s still rests on a reading of what the server runs" % gate)
        g2 = [i for i in items("knowledge/validation_doctrine.md") if i.startswith("- **G2 for a fix.**")][0]
        for said in ("A fix to tests, documentation or CI files alone changes nothing the server runs and has no G2",
                     "Of any other file nobody judges whether the server runs it: that judgement can be wrong"):
            self.assertIn(said, g2)
        g5 = [i for i in items("knowledge/validation_doctrine.md") if i.startswith("- **G5 for a fix.**")][0]
        for said in ("and on the same condition: when it changes anything but tests, documentation and CI files",
                     "One diff decides both gates, by one line (*What a fix changes decides its gates*)",
                     "a fix to tests, documentation or CI files alone has no G5 either"):
            self.assertIn(said, g5)

    def test_the_roles_read_the_diff_by_that_line(self):
        role = self.flat("roles/verify_engineer.md")
        for said in ("when every file it names is a test, a documentation file or a CI file of the repository",
                     '`"G2": "not run: no code the server runs changed"`',
                     "That one diff decides G5 as well",
                     "The line is drawn by the file's path, and by nothing else",
                     "Never judge whether the server runs a file that is none of the three",
                     "a fix to a Dockerfile, a benchmark or tool script or a dependency file is measured like one to a model file",
                     "which the ROCm image does not install",
                     "A fix whose change takes effect outside the worktree",
                     "a package or an environment variable a Dockerfile sets",
                     "Run that gate, and every arm after it, in the environment its tree calls for",
                     "Make the environment the arm's own wherever you can",
                     "Only a package that must replace the image's is installed in place",
                     "First write the image's state and the commands that restore it (`RUN_DIR/env_restore.sh`, and the ledger)",
                     "no server is started ahead and no two arms run side by side",
                     "The reproduction that put the change into effect is stopped, and the image's state back, before the base arm of G2 starts",
                     "check the environment against the ledger's record before the round's first launch",
                     "Install what the change names and nothing else, never the framework again",
                     '`"failure_class": "environment"`',
                     "Never run the fix's arm in the image's environment and call it the fix",
                     "`false` only when every file the fix changes is a test, a documentation file or a CI file"):
            self.assertIn(said, role, "roles/verify_engineer.md does not say it")
        self.assertNotIn("G5 has the narrower line", role, "the verify engineer still judges what the server runs for G5")
        perf = self.flat("roles/perf_engineer.md")
        for said in ("**A fix is measured whatever file it changes,** tests, documentation and CI files apart",
                     "never judge whether the server runs it",
                     "is measured with each arm in the environment its tree calls for, as Verify set them",
                     "the warm-up, each round, the held-out point and the guards alike",
                     "One installed in place is checked against the ledger's record before every launch",
                     "return `not_run` saying so",
                     "never the fix's arm on the image's environment",
                     "is measured as it is, and `summary` says so"):
            self.assertIn(said, perf, "roles/perf_engineer.md does not say it")
        # Every role that runs a fix runs it where its change takes effect, not Verify alone.
        for path, said in (("roles/test_engineer.md", "is tested with every tree in the environment its own files call for"),
                           ("roles/test_engineer.md", "a mutant of the commit in the same, the parent with what the parent declares"),
                           ("roles/test_engineer.md", "Step 4 compares the same way"),
                           ("roles/test_engineer.md", "a check you leave running for the finishing call keeps its environment"),
                           ("roles/test_engineer.md", "Two trees in one environment show nothing"),
                           ("roles/pr_engineer.md", "each tree in the environment its own files call for"),
                           ("roles/pr_engineer.md", "the body gives the setup with the command"),
                           ("roles/director.md", "gives an arm whose change takes effect outside its tree"),
                           ("templates/validation_report.md", "the setup that puts it into effect before the arm's commands")):
            self.assertIn(said, self.flat(path), "%s does not say it" % path)
        # A change that cannot be put into effect was not measured, and no change to the code repairs that: the run
        # stops for a human before anything is investigated.
        src = (ROOT / "inferpilot.js").read_text(encoding="utf-8")
        stop = "const unset = blocking.filter((f) => String(f.failure_class || '').trim().toLowerCase() === 'environment');"
        self.assertIn(stop, src)
        self.assertLess(src.index(stop), src.index("enter('Investigate');"), "an environment failure goes to the investigator")
        review = self.flat("roles/reviewer.md")
        for said in ("`server_code_changed` false means the run measured neither its accuracy nor its speed",
                     "A changed file that is not a test, a documentation file or a CI file is a `blocker` finding",
                     "whether or not the server runs it"):
            self.assertIn(said, review, "roles/reviewer.md does not say it")

    def test_every_place_that_states_the_condition_states_it_by_file(self):
        for path, said in (
                ("knowledge/acceptance.md", "is measured too when it changes anything but tests, documentation and CI files, on the task's `model_path`"),
                ("knowledge/acceptance.md", "is measured too when it changes anything but tests, documentation and CI files, on the task's `client_script`"),
                ("knowledge/issue_triage.md", "A fix that changes anything but tests, documentation and CI files is measured on gsm8k as well"),
                ("knowledge/issue_triage.md", "Such a fix is measured for speed as well (G5 for a fix"),
                ("knowledge/ci_triage.md", "A fix that changes anything but tests, documentation and CI files is measured on gsm8k as well"),
                ("knowledge/ci_triage.md", "Such a fix is measured for speed as well (G5 for a fix"),
                ("knowledge/pr_playbook.md", "A fix that changes anything but tests, documentation and CI files gives the same two scores"),
                ("knowledge/pr_playbook.md", "So does a fix that changes anything but tests, documentation and CI files, the fix next to the base")):
            self.assertIn(said, self.flat(path), "%s does not say it" % path)
        acceptance = (ROOT / "knowledge" / "acceptance.md").read_text(encoding="utf-8")
        for name in ("Accuracy", "Performance"):
            row = [line for line in acceptance.splitlines() if line.startswith("| %s | required" % name)]
            self.assertEqual(len(row), 1, name)
            self.assertIn("required when the fix changes anything but tests, documentation and CI files", row[0], name)
            self.assertNotIn("required when the fix changes code the server runs", row[0], name)


class FixAccuracyTest(unittest.TestCase):
    """The fix of an issue or of a CI failure is measured on gsm8k as a feature is, when it changes code the
    server runs (knowledge/validation_doctrine.md, G2 for a fix). Such a task names its model unless it stops
    before the fix, and the roles and documents say what the fix's score is compared with."""

    @staticmethod
    def flat(path):
        return " ".join((ROOT / path).read_text(encoding="utf-8").split())

    def test_an_issue_or_a_ci_task_that_can_reach_a_fix_names_its_model(self):
        src = (ROOT / "inferpilot.js").read_text(encoding="utf-8")
        early = re.search(r"const BEFORE_A_FIX = \{\n  issue: \[(.*?)\],\n  ci: \[(.*?)\],\n\};", src)
        self.assertIsNotNone(early, "inferpilot.js does not say which phases come before a fix")
        stops = re.search(r"const STOP_PHASES = \{(.*?)\n\};", src, re.S).group(1)
        for mode, listed in zip(("issue", "ci"), early.groups()):
            phases = [p.lower() for p in re.findall(r"'(\w+)'", re.search(r"\n  %s: \[(.*?)\]," % mode, stops).group(1))]
            self.assertEqual(re.findall(r"'(\w+)'", listed), phases[:phases.index("implement")],
                             "%s: a run that stops before Implement has no fix, and one that reaches it has" % mode)
        self.assertIn("const fixes = !!BEFORE_A_FIX[mode] && !BEFORE_A_FIX[mode].includes(stop);", src)
        self.assertIn("if (fixes && !a.model_path) {\n    ask('model_path',", src)
        self.assertIn("const fix = (MODE === 'issue' || MODE === 'ci') && !!C.model_path;\n"
                      "  if ((MODE === 'feature' || MODE === 'perf' || fix) && !C.accuracy_script) {", src,
                      "a fix G2 will measure may start without the accuracy script")
        self.assertIn("if (fix && !C.server_script) {", src, "or without a server script to serve it with")

    def test_no_fix_starts_without_a_model(self):
        """A stop before the fix lets a run start without one; the route can skip that stop's phase (an existing
        fix PR has no Diagnose), and the control file can lift it."""
        src = (ROOT / "inferpilot.js").read_text(encoding="utf-8")
        self.assertIn("function askForFix(at) {\n  const lacks = ['model_path', 'client_script'].filter((k) => !C[k]);", src)
        self.assertRegex(src, r"const FIX_NEEDS = \{ model_path: [^}]*\};", "the model is not among what a fix needs")
        for at in ("Reproduce", "Diagnose", "Bisect"):
            self.assertIn("const lacking = askForFix('%s');\n" % at, src, "a fix can start after %s without a model" % at)
        self.assertRegex(src, r"if \(tri\.action === 'push_existing_pr'\) \{\n(?:\s*//.*\n)*\s*if \(stopHere\('Diagnose'\)\) \{",
                         "a stop after Diagnose does not hold for an existing fix PR, which has no Diagnose")
        for name, text in (("README.md", EN), ("README.zh-CN.md", ZH)):
            self.assertRegex(text, r"reaches its\s+fix without\s+one|走到修复却还没有模型", "%s does not list the question" % name)
        fields = (ROOT / "templates" / "task_definition.md").read_text(encoding="utf-8")
        row = [line for line in fields.splitlines() if line.startswith("| `model_path` |")]
        self.assertEqual(len(row), 1)
        self.assertEqual(row[0].count("required unless `stop_after` ends the run before a fix"), 2,
                         "templates/task_definition.md: an issue and a ci task each name a model")
        for name, text in (("README.md", EN), ("README.zh-CN.md", ZH)):
            row = [line for line in text.splitlines() if line.startswith("| `model_path` |")]
            self.assertEqual(len(row), 1, name)
            for said in ("`issue`", "`ci`", "`stop_after`", "gsm8k"):
                self.assertIn(said, row[0], "%s: the model_path row" % name)

    def test_a_fix_is_measured_when_it_changes_code_the_server_runs_and_says_what_it_is_compared_with(self):
        note = [i for i in items("knowledge/validation_doctrine.md") if i.startswith("- **G2 for a fix.**")]
        self.assertEqual(len(note), 1, "knowledge/validation_doctrine.md does not say how a fix is measured")
        for said in ("when it changes anything but tests, documentation and CI files", "the fix at most 1 point below the base", "has no G2",
                     "a model that answers no text questions", "The reproduction or the failing test runs first",
                     "it is never launched for G2", "the last commit that served the configuration",
                     "the culprit's parent for a CI fix", "The gate and the pull request name that commit", "it is reported"):
            self.assertIn(said, note[0])
        for path, said in (("roles/verify_engineer.md", "not run: no code the server runs changed"),
                           ("roles/verify_engineer.md", "not run: the model answers no text questions"),
                           ("roles/verify_engineer.md", "That gate of `extra_gates` runs first"),
                           ("roles/verify_engineer.md", "launch nothing for G2 until its gate of `extra_gates` has passed"),
                           ("roles/verify_engineer.md", "never launch it for G2"),
                           ("roles/verify_engineer.md", "Write that commit in the ledger and in `gates`"),
                           ("roles/verify_engineer.md", "it is reported, not judged"),
                           ("roles/director.md", "the verify engineer measures the base arm"),
                           ("roles/director.md", "when the task has a `model_path`, render InferPilot's server template"),
                           ("roles/director.md", "the one template a run takes without the user's answer, and say so in `notes`"),
                           ("templates/task_definition.md", "It is the one case in which a template runs without the user's answer"),
                           ("templates/task_definition.md", "it is not chosen for the user, but for the server a fix is measured on "
                                                            "(G2, G5) when the report or the job starts none"),
                           ("roles/director.md", "Return the one or the other as `server_script`"),
                           ("roles/director.md", "without one, skip them as well"),
                           ("roles/director.md", "for the G2 and the G5 of a fix whose baseline was measured on another commit"),
                           ("templates/validation_report.md", "the commit its baseline was measured on when that was not the base"),
                           ("roles/intake.md", "An issue or a ci task needs it too"),
                           ("knowledge/acceptance.md", "required when the fix changes anything but tests, documentation and CI files"),
                           ("knowledge/issue_triage.md", "A fix that changes anything but tests, documentation and CI files is measured on gsm8k as well"),
                           ("knowledge/ci_triage.md", "A fix that changes anything but tests, documentation and CI files is measured on gsm8k as well"),
                           ("knowledge/pr_playbook.md", "A fix that changes anything but tests, documentation and CI files gives the same two scores")):
            self.assertIn(said, self.flat(path), "%s does not say it" % path)
        acceptance = (ROOT / "knowledge" / "acceptance.md").read_text(encoding="utf-8")
        row = [line for line in acceptance.splitlines() if line.startswith("| Accuracy | required: gsm8k")]
        self.assertEqual(len(row), 1, "knowledge/acceptance.md §2 has no Accuracy row")
        for stale in ("when the issue is an accuracy loss", "| no regression |"):
            self.assertNotIn(stale, row[0], "the profiles still leave a fix's accuracy to the kind of issue")


class OutputComparisonTest(unittest.TestCase):
    """gsm8k cannot be put to a model that answers no text questions (a diffusion, speech or embedding model). The
    fix of an issue or of a CI failure on one is measured by an output comparison in its place: the task's own
    test, its `output_script`, on the same inputs and seeds for the base and for the fix
    (knowledge/validation_doctrine.md, G2 for a model that answers no text questions). InferPilot has no template
    for one and no role writes one: Setup says what the model is, and a task that can reach a fix without the
    script is asked for it."""

    FIELD = "output_script"

    @staticmethod
    def flat(path):
        return " ".join((ROOT / path).read_text(encoding="utf-8").split())

    def test_the_task_names_the_script_and_no_fix_starts_without_it(self):
        src = (ROOT / "inferpilot.js").read_text(encoding="utf-8")
        keys = re.search(r"const TASK_KEYS = \[(.*?)\];", src, re.S).group(1)
        intake = re.search(r"const TASK_DEF = S\(\{(.*?)\n\}\);", src, re.S).group(1)
        self.assertIn("'%s'" % self.FIELD, keys, "inferpilot.js does not take the script")
        self.assertIn("\n  %s: str," % self.FIELD, intake, "the intake cannot return the script")
        self.assertIn("\n  %s: String(A.%s || '').trim()," % (self.FIELD, self.FIELD), src, "the script does not reach the roles")
        use = re.search(r"\n  whenToUse: '(.*)',\n", src).group(1)
        self.assertIn("args.%s (issue, ci: the user\\'s own test that compares the outputs of a model that answers no text questions" % self.FIELD,
                      use, "inferpilot.js's whenToUse does not tell the caller of the script")
        self.assertIn("asked for it after Setup, since InferPilot has no template for it", use)
        # The base is not always the reference (validation_doctrine.md, G2 for a model that answers no text questions).
        self.assertIn("the fix\\'s with a baseline\\'s, which is the base, or the last commit that served the configuration where the base "
                      "cannot or where its outputs are the failure being fixed", use, "whenToUse makes the base the reference in every case")
        self.assertIn("where the run knows no such commit, the fix\\'s outputs are kept and reported, not judged", use)
        self.assertNotIn("on the base and on the fix; G2 runs it", use)
        for schema, who in (("SETUP", "Setup"), ("VERIFY", "the verify engineer")):
            body = re.search(r"\nconst %s = S\(\{(.*?)\n\}, \[" % schema, src, re.S).group(1)
            self.assertIn("text_model: bool,", body, "%s cannot say that the model answers no text questions" % who)
        self.assertIn("if (BEFORE_A_FIX[MODE]) C.text_model = s.text_model !== false;", src,
                      "what Setup says of the model does not reach the roles; left out, the model answers text questions")
        self.assertIn("function outputsLacking() {\n  if (C.text_model !== false || C.output_script) return null;", src)
        # Asked after Setup of a task that can reach a fix, at the fix of a run whose stop before it was lifted, and
        # a round that finds such a model without the script passes nothing.
        self.assertRegex(src, r"if \(stopHere\('Setup'\)\) return finish\('stopped', 'Setup'\);\n(?:\s*//.*\n)*\s*const stop = liveStopAfter\(\);\n"
                              r"\s*const lacking = BEFORE_A_FIX\[MODE\] && stop !== 'now' && !BEFORE_A_FIX\[MODE\]\.includes\(stop\) \? outputsLacking\(\) : null;",
                         "a task that can reach a fix is not asked for the script after Setup")
        self.assertIn("const outputs = outputsLacking();\n  if (outputs) lacks.push('output_script');", src,
                      "a run whose stop before the fix was lifted starts its fix without the script")
        # The verify engineer has the last word on what the model is, either way; a round that does not say changes nothing.
        self.assertIn("if (BEFORE_A_FIX[MODE] && typeof v.text_model === 'boolean') C.text_model = v.text_model;\n"
                      "    if (C.server_code_changed && outputsLacking()) {", src,
                      "a round that finds such a model without the script can pass")
        # With the script, a round is accepted only on the comparison's result: not on a G2 it left out or did not run.
        self.assertIn("const compared = String((v.gates || {}).G2 || '').trim();\n", src)
        # "no baseline" is the documented result and nothing looser: the outputs kept and where, never a G2 that did not run or failed
        self.assertIn("const keptOutputs = /\\bno baseline\\b/i.test(compared) && /\\boutputs\\/fix-/.test(compared) "
                      "&& !/^(not[ _-]?run|fail|skip)/i.test(compared);\n"
                      "    const comparisonFailed = failures.some((f) => String(f.gate || '').trim() === 'G2');\n"
                      "    if (C.server_code_changed && C.text_model === false && (v.all_pass === true || (failures.length && !blocking.length))\n"
                      "      && (comparisonFailed || !(/^pass\\b/i.test(compared) || keptOutputs))) {", src,
                      "a round can be accepted for such a model without the result of its output comparison")
        # a difference the comparison reports is never the baseline's to keep: no known issue, and a human decides
        self.assertIn("if (C.text_model === false && C.output_script && blocking.some((f) => String(f.gate || '').trim() === 'G2')) {", src,
                      "a known issue can excuse the output comparison in a later round")
        self.assertIn("\nFIX_NEEDS.output_script = ", src, "the result does not say what to give")
        question = re.search(r"\nconst OUTPUTS_ASK = (.*?);\n", src, re.S).group(1)
        for said in ("PORT", "OUT_DIR", "BASE_DIR", "same inputs with the same seeds", "exits non-zero", "no template for it"):
            self.assertIn(said, question, "the question does not say what the script does")
        # A value that names no script would win over the answer and be asked about again: Intake asks first.
        self.assertIn("if (BEFORE_A_FIX[mode] && outputs && (!outputs.trim() || isTemplate(outputs))) {\n    ask('output_script', OUTPUTS_ASK,", src)

    def test_the_documents_say_how_such_a_fix_is_measured(self):
        notes = items("knowledge/validation_doctrine.md")
        note = [i for i in notes if i.startswith("- **G2 for a model that answers no text questions.**")]
        self.assertEqual(len(note), 1, "knowledge/validation_doctrine.md does not say how such a fix is measured")
        for said in ("Its G2 is an output comparison", "its `output_script`", "the same inputs with the same seeds to the base and to the fix, each once",
                     "by the measure and within the tolerance the script fixes", "The test is the user's, as G3's is",
                     "InferPilot has no template for one, and no role writes one in its place", "Setup says what the model is",
                     "is asked for it then, before anything else runs", "by the fix's files and after the reproduction or the failing test",
                     "the last commit that served the configuration where the base cannot",
                     "Where the failure being fixed is in these very outputs", "kept and reported, not judged",
                     "is a blocking failure, and so is a run that compared nothing", "This is the one G2 that compares outputs",
                     "A round is accepted only on the comparison's result, a pass or outputs kept where there is no baseline",
                     "never on a G2 that did not run, with the script named or without",
                     "No known issue excuses a difference the comparison reports",
                     "where the base differs from itself as much, the script cannot tell the fix from noise",
                     "Each run of the fix writes to a directory of its own, named for its commit"):
            self.assertIn(said, note[0])
        fix = [i for i in notes if i.startswith("- **G2 for a fix.**")]
        self.assertIn("is measured by an output comparison in its place (*G2 for a model that answers no text questions*)", fix[0])
        self.assertNotIn("Nor has the fix for a model that answers no text questions", fix[0], "the doctrine still leaves such a fix unmeasured")
        for path, said in (("roles/verify_engineer.md", "G2 is an output comparison in its place, by the task's own test, its `output_script`"),
                           ("roles/verify_engineer.md", "Run it unchanged, once per arm"),
                           ("roles/verify_engineer.md", "PORT=<port> OUT_DIR=RUN_DIR/outputs/base bash <output_script>"),
                           # every run of the fix writes to a directory of its own; only the baseline's is reused
                           ("roles/verify_engineer.md", "PORT=<port> OUT_DIR=RUN_DIR/outputs/fix-<commit> BASE_DIR=RUN_DIR/outputs/base bash "
                                                        "<output_script>"),
                           ("roles/verify_engineer.md", "in a directory that no earlier execution wrote to, named for the commit it runs: `mkdir "
                                                        "RUN_DIR/outputs/fix-<commit>`"),
                           ("roles/verify_engineer.md", "a commit that runs again takes `fix-<commit>-2`"),
                           ("roles/verify_engineer.md", "A retry or a repaired fix never writes where an earlier execution wrote"),
                           ("roles/verify_engineer.md", "Only the baseline's directory is reused, for the commit it was measured on"),
                           ("roles/verify_engineer.md", "16 images of 5d6e7f8 written to RUN_DIR/outputs/fix-5d6e7f8, no baseline"),
                           ("roles/verify_engineer.md", "InferPilot accepts a round for such a model only on one of those two results, a `pass` or "
                                                        "outputs kept with `no baseline`"),
                           ("roles/verify_engineer.md", "never report it `not run` where the task names the script"),
                           ("roles/verify_engineer.md", "The second is that form and nothing looser: it names the outputs that were kept and the "
                                                        "directory they are in"),
                           ("roles/verify_engineer.md", "a G2 that begins `not run` or `fail` is neither, whatever it says of a baseline"),
                           # the known-issue rule of step 4 does not apply to the comparison
                           ("roles/verify_engineer.md", "Step 4 excuses nothing here: the comparison runs on fixed inputs and seeds"),
                           ("roles/verify_engineer.md", "no entry of `known_issues` makes an output that differs by more than the script tolerates "
                                                        "pass, or fail without blocking"),
                           ("roles/verify_engineer.md", "a comparison listed among the round's failures is not accepted"),
                           ("roles/verify_engineer.md", "the run stops for a human, and records no known issue"),
                           ("roles/verify_engineer.md", 'say so in `gates` and with `"text_model": true` in your answer'),
                           ("roles/verify_engineer.md", '`"text_model": true` when a model Setup took for such a one answers text questions after all'),
                           ("roles/verify_engineer.md", "What you say replaces what Setup said, for the later rounds and the report"),
                           ("roles/verify_engineer.md", "Never write such a test yourself, and never change the task's"),
                           ("roles/verify_engineer.md", "never a pass on its exit status alone"),
                           ("roles/verify_engineer.md", "Where the failure being fixed is in these outputs"),
                           ("roles/verify_engineer.md", "where one Setup took for such a model answers text questions all the same"),
                           ("roles/verify_engineer.md", 'Say `"text_model": false` in your answer'),
                           ("roles/verify_engineer.md", "not run: the model answers no text questions, and the task names no output_script"),
                           ("roles/verify_engineer.md", "Never pass G2 for such a fix on the reproduction or the failing test alone"),
                           ("roles/verify_engineer.md", 'It adds `"text_model": false` when the model answers no text questions'),
                           ("roles/director.md", "`issue`, `ci`, with a `model_path`: say what the model is"),
                           ("roles/director.md", '`"text_model": false` for one that answers no text questions'),
                           ("roles/director.md", "a model that takes images or audio and answers in text included"),
                           ("roles/director.md", "Judge by the model's files and by the command the report or the job serves it with, not by its name"),
                           ("roles/director.md", "a run that can reach a fix without one stops after Setup and asks for it"),
                           ("roles/director.md", "Never write that test in the task's place"),
                           ("roles/director.md", 'An issue or a ci task with a `model_path` adds `"text_model": true | false`'),
                           ("roles/director.md", "and `output_script` and `long_context_script`, for which there is no template) run as given"),
                           ("roles/intake.md", "`output_script` (`task_definition.md` §1.1): the user's own test that compares the outputs"),
                           ("roles/intake.md", "Ask for it when the request names none, and offer nothing in its place"),
                           ("roles/intake.md", "Never ask it of a model that answers text questions"),
                           ("roles/intake.md", "G2 compares its outputs with the task's `output_script`"),
                           ("roles/perf_engineer.md", "That is the commit G2 measured against, which the ledger names"),
                           ("knowledge/acceptance.md", "is measured by an output comparison in gsm8k's place: the task's own test, its `output_script`"),
                           ("knowledge/ci_triage.md", "is measured by the task's own output comparison in gsm8k's place (`output_script`"),
                           ("knowledge/issue_triage.md", "the task's own output comparison stands in gsm8k's place (`output_script`"),
                           ("knowledge/pr_playbook.md", "for a model that answers no text questions, what the task's own test compared in their place"),
                           ("templates/task_definition.md", "**`output_script`** is the task's own test of the outputs of a model that answers no text questions"),
                           ("templates/task_definition.md", "With `BASE_DIR` set, the directory its run on the baseline wrote to, it compares its outputs with those"),
                           ("templates/task_definition.md", "exits non-zero when one differs by more than it tolerates"),
                           ("templates/task_definition.md", "It has no template for one, and writes none")):
            self.assertIn(said, self.flat(path), "%s does not say it" % path)
        for path in ("roles/verify_engineer.md", "roles/intake.md", "knowledge/acceptance.md", "knowledge/ci_triage.md"):
            for stale in ("What its outputs are worth is the reproduction's or the failing test's to show", "G2 says that gsm8k cannot be put to it",
                          "nor one for a model that answers no text questions"):
                self.assertNotIn(stale, self.flat(path), "%s still leaves such a fix unmeasured" % path)
        # The playbook said that the fix for such a model gives no table: it gives what the comparison showed. Read in
        # the note on accuracy, where the clause stood: the note on speed has a sentence much like it.
        accuracy = [i for i in items("knowledge/pr_playbook.md") if i.startswith("- **Accuracy Tests:**")]
        self.assertEqual(len(accuracy), 1, "knowledge/pr_playbook.md has no note on accuracy tests")
        self.assertNotIn("one for a model that answers no text questions says that instead", accuracy[0],
                         "knowledge/pr_playbook.md still has such a fix say that it changes nothing the server runs")
        self.assertIn("A fix to tests, documentation or CI files alone says that it changes no code the server runs, and gives no table. "
                      "The fix of an accuracy problem in CI", accuracy[0])
        self.assertIn("for a model that answers no text questions, what the task's own test compared in their place", accuracy[0])

    def test_the_task_definition_and_both_readmes_list_the_script(self):
        fields = (ROOT / "templates" / "task_definition.md").read_text(encoding="utf-8")
        row = [line for line in fields.splitlines() if line.startswith("| `%s` |" % self.FIELD)]
        self.assertEqual(len(row), 1, "templates/task_definition.md has no row for the script")
        self.assertEqual(row[0].count("| required for such a model unless `stop_after` ends the run before a fix "), 2,
                         "an issue and a ci task each name it for a model that answers no text questions")
        for name, text, said in (("README.md", EN, ("`BASE_DIR`", "`OUT_DIR`", "InferPilot has no template for it", "once on the base and once on the fix")),
                                 ("README.zh-CN.md", ZH, ("`BASE_DIR`", "`OUT_DIR`", "InferPilot 没有这种脚本的模板", "base 和修复各跑一次"))):
            row = [line for line in text.splitlines() if line.startswith("| `%s` |" % self.FIELD)]
            self.assertEqual(len(row), 1, "%s has no row for the script" % name)
            for words in said:
                self.assertIn(words, row[0], "%s: the output_script row" % name)
        self.assertRegex(EN, r"the question after Setup\s+for the script that compares outputs \(`output_script`\)", "README.md does not list the question")
        self.assertIn("Setup 之后会问比较输出的脚本（`output_script`）", ZH, "README.zh-CN.md does not list the question")


class FixPerformanceTest(unittest.TestCase):
    """The fix of an issue or of a CI failure is measured for speed as it is for accuracy, and on the same condition
    (knowledge/validation_doctrine.md, G5 for a fix): when it changes code the server runs, it is no slower than the
    base on the task's client. A performance problem is measured whatever its fix changes: its number comes back.
    So such a task names its client unless it stops before the fix, and the roles and documents say so."""

    @staticmethod
    def flat(path):
        return " ".join((ROOT / path).read_text(encoding="utf-8").split())

    def test_an_issue_or_a_ci_task_that_can_reach_a_fix_names_its_client(self):
        src = (ROOT / "inferpilot.js").read_text(encoding="utf-8")
        self.assertIn("if ((g4 || fixes) && !a.client_script) {\n", src, "a fix G5 will measure may start without a client")
        self.assertIn("if ((g4 || fixes) && gaps.length) {\n    ask('workload',", src,
                      "InferPilot's client would measure a fix on lengths or a concurrency nobody chose")
        self.assertIn("const FIX_NEEDS = { model_path: 'the model', client_script: 'the client', "
                      "workload: \"the whole workload of InferPilot's client\" };", src,
                      "a run that reaches its fix without a client is not asked for one")
        # Intake asks nothing about the template's workload of a task that stops before its fix: where that stop
        # is lifted, the fix must not be measured on what Setup rendered from a part of a workload.
        self.assertIn("  const gaps = isTemplate(A.client_script) ? templateLoadGaps(A) : [];\n  if (gaps.length) lacks.push('workload');\n",
                      src, "a fix can start on InferPilot's client with lengths or a concurrency nobody chose")
        self.assertIn("const partial = R.questions.some((q) => q.field === 'workload') && A.workload ? { workload: '' } : {};\n"
                      "    out.rerun_with = { ...partial, answers:", src,
                      "the part of a workload the task gave wins over the answer on the next run")
        self.assertIn("Where the control file lifts that stop, the run asks before any code changes for what the fix lacks: the "
                      "model, the client, and of a task that names InferPilot's client all the same, what its `workload` leaves out.",
                      self.flat("templates/task_definition.md"))
        for mode in ("issue", "ci"):
            for table in ("ROUTES", "STOP_PHASES"):
                body = re.search(r"const %s = \{(.*?)\n\};" % table, src, re.S).group(1)
                phases = re.findall(r"'(\w+)'", re.search(r"\n  %s: \[(.*?)\]," % mode, body).group(1))
                self.assertEqual(phases[phases.index("Verify"):phases.index("Review") + 1], ["Verify", "Performance", "Review"],
                                 "%s: %s has no Performance between Verify and Review" % (table, mode))
        use = re.search(r"\n  whenToUse: '(.*)',\n", src).group(1)
        self.assertIn("args.client_script (required for feature and perf, where G5 measures every change, and for an issue or a ci "
                      "task that can reach a fix", use)
        self.assertNotIn("for a feature with a workload", use, "whenToUse still gives the client's old rule")
        fields = (ROOT / "templates" / "task_definition.md").read_text(encoding="utf-8")
        row = [line for line in fields.splitlines() if line.startswith("| `client_script` |")]
        self.assertEqual(len(row), 1)
        self.assertIn("| required unless `stop_after` ends the run before a fix: G5 measures the fix on it; with `stability` on, "
                      "required either way | as for an issue |", row[0], "templates/task_definition.md: an issue's and a ci task's client")
        for name, text in (("README.md", EN), ("README.zh-CN.md", ZH)):
            row = [line for line in text.splitlines() if line.startswith("| `client_script` |")]
            self.assertEqual(len(row), 1, name)
            for said in ("`issue`", "`ci`", "`stop_after`", "G5"):
                self.assertIn(said, row[0], "%s: the client_script row" % name)
            self.assertRegex(text, r"a model or a client, when an issue or a ci run reaches its\s+fix without\s+one"
                             r"|走到修复却还没有模型或 client", "%s does not list the question" % name)

    def test_a_fix_is_measured_when_it_changes_code_the_server_runs_and_a_performance_problem_always(self):
        src = (ROOT / "inferpilot.js").read_text(encoding="utf-8")
        for said, why in (
                ("const NOT_MEASURED = 'not run: no code the server runs changed';", "the run record does not say why G5 was not run"),
                ("const unmeasured = (when) => when === 'fix' && C.server_code_changed === false;",
                 "a fix is left unmeasured on anything but the verify engineer's word about its diff"),
                ("if (BEFORE_A_FIX[MODE]) C.server_code_changed = v.server_code_changed !== false;",
                 "what the last Verify round says of the diff does not reach Performance, or a round that says nothing leaves a fix unmeasured"),
                ("const issueClaim = perfIssue ? String(rep.perf_claim || '').trim() : NO_SLOWER;\n  const runPerf = perfIssue ? true : 'fix';",
                 "an issue's fix is not measured on the right claim"),
                ("const ciClaim = perfFailure ? String(cl.perf_claim || '').trim() : NO_SLOWER;", "a ci fix is not measured on the right claim"),
                ("ciClaim, perfFailure ? true : 'fix');", "a performance failure is not measured whatever its fix changes"),
                ("const unjudged = trusted && BEFORE_A_FIX[MODE] && claim === NO_SLOWER ? String(p.no_baseline || '').trim() : '';",
                 "no baseline excuses more than a fix that claims no gain")):
            self.assertIn(said, src, why)
        for schema, field in (("VERIFY", "server_code_changed: bool,"), ("CLASSIFY", "performance: bool,"), ("CLASSIFY", "perf_claim: str,"),
                              ("PERF", "no_baseline: str,")):
            body = re.search(r"\nconst %s = S\(\{(.*?)\n\}, \[" % schema, src, re.S).group(1)
            self.assertIn(field, body, "%s does not take %s" % (schema, field))
        note = [i for i in items("knowledge/validation_doctrine.md") if i.startswith("- **G5 for a fix.**")]
        self.assertEqual(len(note), 1, "knowledge/validation_doctrine.md does not say how a fix is measured for speed")
        for said in ("when it changes anything but tests, documentation and CI files", "no slower than the base beyond the noise band",
                     "One diff decides both gates", "has no G5 either", "the last commit that served it", "where G2 did not run, the one the triage record names",
                     "reported and not judged", "names its client as it names its model",
                     "no template is offered for it", "A performance problem is measured whatever its fix changes",
                     "a CI failure on a number its test measures", "A threshold moved in a test restores nothing",
                     "stops the task at Performance for a human, and nothing is rolled back"):
            self.assertIn(said, note[0])
        for path, said in (("roles/verify_engineer.md", 'and the answer `"server_code_changed": false`'),
                           ("roles/verify_engineer.md", "That one diff decides G5 as well"),
                           ("roles/verify_engineer.md", "Read the diff in every round"),
                           ("roles/perf_engineer.md", "**A fix (issue, ci) has no baseline from Setup:** measure both arms yourself"),
                           ("roles/perf_engineer.md", "the baseline is the last commit that served it, in a throwaway worktree. That "
                                                      "is the commit G2 measured against, which the ledger names"),
                           ("roles/perf_engineer.md", "Where G2 measured none, the commit is still known: take it from the triage record"),
                           ("roles/perf_engineer.md", "Only where the run knows no such commit, measure the fix alone"),
                           ("roles/perf_engineer.md", "return `no_baseline` with the reason"),
                           ("roles/perf_engineer.md", "**A performance problem is measured where it showed:**"),
                           ("roles/perf_engineer.md", "a feature or a fix that is slower does not"),
                           ("roles/ci_guardian.md", "`performance`: true when the test fails on a number it measures"),
                           ("roles/ci_guardian.md", "Without one the task stops here"),
                           ("roles/issue_triager.md", "`performance` makes the reported number the claim G5 judges the fix on, whatever the "
                                                      "fix changes"),
                           ("roles/director.md", "Nor does its speed (*G5 for a fix*): the performance engineer measures both arms after "
                                                 "Verify"),
                           ("roles/director.md", "for the G2 and the G5 of a fix whose baseline was measured on another commit"),
                           ("roles/intake.md", "An issue or a ci task needs a client too, unless `stop_after` ends the run before a fix"),
                           ("roles/intake.md", "offer no `template` for it"),
                           ("roles/reviewer.md", "`server_code_changed` false means the run measured neither its accuracy nor its speed"),
                           ("templates/task_definition.md", "So does an issue or a ci task that can reach a fix"),
                           ("templates/task_definition.md", "G2 and G5 then serve the fix of a task that has a `model_path` with "
                                                            "`server.sh`"),
                           ("templates/ci_triage.md", "| performance problem: yes / no |"),
                           ("templates/ci_triage.md", "for a performance problem the number the fix restores, whatever it changes"),
                           ("templates/issue_triage.md", "its speed on the task's client against the same commit (G5)"),
                           ("templates/validation_report.md", "for the G2 and the G5 of a fix"),
                           ("templates/validation_report.md", "`not run: no code the server runs changed`, and no table"),
                           ("knowledge/acceptance.md", "**A fix** (issue, ci) is measured too when it changes anything but tests, documentation "
                                                       "and CI files, on the task's `client_script`"),
                           ("knowledge/acceptance.md", "always when the failure is a performance problem: the number its test measures comes "
                                                       "back"),
                           ("knowledge/perf_evaluation.md", "The fix of an issue or of a CI failure is measured the same way when it changes "
                                                            "code the server runs"),
                           ("knowledge/issue_triage.md", "Such a fix is measured for speed as well (G5 for a fix"),
                           ("knowledge/ci_triage.md", "Such a fix is measured for speed as well (G5 for a fix"),
                           ("knowledge/ci_triage.md", "**A failure on a number is a performance problem,** whatever its class"),
                           ("knowledge/pr_playbook.md", "So does a fix that changes anything but tests, documentation and CI files, the fix next "
                                                         "to the base"),
                           ("knowledge/dev_methodology.md", "nor is a fix that changes code the server runs slower than the base")):
            self.assertIn(said, self.flat(path), "%s does not say it" % path)
        for name, text in (("README.md", EN), ("README.zh-CN.md", ZH)):
            modes = [line for line in text.splitlines() if re.match(r"\| `(issue|ci)` \|", line) and "Setup →" in line]
            self.assertEqual(len(modes), 2, name)
            for line in modes:
                self.assertRegex(line, r"Verify → Performance[ （(]", "%s: a fix's Performance phase, and when it is skipped" % name)
            self.assertRegex(text, r"its number must come back, whatever the fix changes|掉下去的数值都必须回来", name)


class CiAccuracyProblemTest(unittest.TestCase):
    """A CI failure on a score its test measures is an accuracy problem, as one on a speed number is a
    performance problem (knowledge/validation_doctrine.md, G2 for an accuracy problem in CI). It is fixed when
    the score comes back: G2 measures it whatever the fix changes, and a threshold moved in the test restores
    nothing."""

    @staticmethod
    def flat(path):
        return " ".join((ROOT / path).read_text(encoding="utf-8").split())

    def test_the_run_takes_the_score_from_classify_and_accepts_no_round_without_g2(self):
        src = (ROOT / "inferpilot.js").read_text(encoding="utf-8")
        body = re.search(r"\nconst CLASSIFY = S\(\{(.*?)\n\}, \[", src, re.S).group(1)
        for field in ("accuracy: bool,", "accuracy_claim: str,"):
            self.assertIn(field, body, "CLASSIFY does not take %s" % field)
        for said, why in (
                ("const accClaim = accFailure ? String(cl.accuracy_claim || '').trim() : '';", "the score to restore is not read"),
                ("if (!handOver && accFailure && !accClaim) {", "an accuracy failure goes on without the score its fix must restore"),
                ("if (!handOver && accClaim) C.accuracy_claim = accClaim;", "the claim does not reach the verify engineer"),
                ("const claimGate = String((v.gates || {}).accuracy_claim || '').trim();",
                 "the score to restore is read from G2, which also holds gsm8k against the base"),
                ("if (C.accuracy_claim && (v.all_pass === true || (failures.length && !blocking.length)) && !/^pass\\b/i.test(claimGate)) {",
                 "a round is accepted without the score's pass: both ways a round is accepted are held to it, and only \"pass\" is one"),
                ("const ownGates = [...(extraGates || []).map((g) => g.gate), ...(C.accuracy_claim ? ['accuracy_claim'] : [])];",
                 "the fix's own gates are not told from the others"),
                ("const mine = own(blocking).map((f) => f.gate);", "a gate of the fix's own that fails on the base becomes a known issue")):
            self.assertIn(said, src, why)
        # The check sits before the line that accepts a round, whichever way it is accepted; and what the task is
        # there to fix is never recorded as a known issue, which the next round would excuse.
        self.assertLess(src.index("if (C.accuracy_claim && (v.all_pass === true"), src.index("note('Verify', 'pass',"))
        self.assertLess(src.index("const mine = own(blocking).map((f) => f.gate);"), src.index("if (inv.known_issue) R.known_issues.push(inv.known_issue);"))

    def test_the_bullet_is_a_bullet_of_its_step(self):
        role = (ROOT / "roles" / "verify_engineer.md").read_text(encoding="utf-8").splitlines()
        line = [l for l in role if "**An accuracy problem is measured where it showed, whatever the fix changes.**" in l]
        self.assertEqual(len(line), 1)
        self.assertTrue(line[0].startswith("   - **An accuracy problem"), "the bullet is not indented as its siblings in step 2: %r" % line[0][:40])

    def test_the_documents_say_what_is_measured(self):
        note = [i for i in items("knowledge/validation_doctrine.md") if i.startswith("- **G2 for an accuracy problem in CI.**")]
        self.assertEqual(len(note), 1, "knowledge/validation_doctrine.md does not say how a CI accuracy problem is judged")
        for said in ("A CI failure on a score its test measures", "It is fixed when the score comes back, so the score is the claim",
                     "`accuracy_claim`: the set, the model and the value", "with the failing test's own measurement, whatever the fix changes",
                     "G2 reads the score, not the assertion", "a threshold moved in a test restores nothing",
                     "No known issue excuses it: the base scores low as well",
                     "It is reported under its own name (`accuracy_claim`), apart from gsm8k against the base",
                     "That measurement is the base's: the set, the number of questions, the model and the sampling settings as the test has "
                     "them before the fix",
                     "nor do fewer questions or another sampling",
                     "The pull request shows the score that fell and the score restored, whatever the fix changes",
                     "cannot stand in for it",
                     "that is a human's to say"):
            self.assertIn(said, note[0])
        for path, said in (
                ("knowledge/ci_triage.md", "**A failure on a score is an accuracy problem,** whatever its class"),
                ("knowledge/ci_triage.md", "a lower threshold in the test is not a fix"),
                ("roles/ci_guardian.md", "`accuracy`: true when the test fails on a score it measures"),
                ("roles/ci_guardian.md", '"accuracy": false, "accuracy_claim": "<accuracy failures only>"'),
                ("roles/verify_engineer.md", "**An accuracy problem is measured where it showed, whatever the fix changes.**"),
                ("roles/verify_engineer.md", "read the score it measures, not whether its assertion passes"),
                ("roles/verify_engineer.md", "Report the score in `gates` under its own name, apart from G2's gsm8k against the base"),
                ("roles/verify_engineer.md", '`"accuracy_claim": "pass 0.936, the claim is 0.93 (the test\'s GSM8K, 200 q, as at the base)"`'),
                ("roles/verify_engineer.md", "with `pass` only for a score at the claim or above by that measurement"),
                ("roles/verify_engineer.md", "Never leave the gate out, and never report it `not run`: the run accepts no round without its `pass`"),
                # the measurement is the base's: a fix can change what is measured as easily as the threshold
                ("roles/verify_engineer.md", "The measurement is the base's: the set, the number of questions, the model and the sampling settings as the "
                                             "test has them at `<base>`"),
                ("roles/verify_engineer.md", "What is kept from the base is everything that decides the score, wherever it lives"),
                ("roles/verify_engineer.md", "What is kept from the fix is its framework code and how it launches the server"),
                ("roles/verify_engineer.md", "run the base's measurement against the fix's server"),
                ("roles/verify_engineer.md", "nor do fewer questions or another sampling"),
                ("roles/verify_engineer.md", "A fix that changes the measurement is a human's to accept, whether or not it says that the old "
                                             "one was wrong"),
                ("roles/verify_engineer.md", "wherever the fix's measurement differs from the base's (another set, number of questions, model or "
                                             "sampling), measure both ways and give both scores, in `gates` and in the ledger"),
                ("knowledge/validation_doctrine.md", "Both are measured whenever the fix changes the measurement, whether or not it says that the old "
                                                     "one was wrong"),
                # a failing test that still fails ends the round, and the scores are given all the same: whoever decides next needs them
                ("roles/verify_engineer.md", "A `failing_test` that still fails with the fix ends the round (step 2) and stays its blocking failure, "
                                             "and the gates after it stay `not run`; this measurement is not one of them"),
                ("roles/verify_engineer.md", "before you return, give the score the fix reached, both ways where the fix changes the measurement, in "
                                             "`gates`, in the ledger and as that failure's `evidence`"),
                ("roles/verify_engineer.md", '`"accuracy_claim": "fail 0.871 by the base\'s measurement (200 q), 0.902 by the fix\'s (500 q); the claim '
                                             'is 0.93"`'),
                ("roles/verify_engineer.md", "Only where there is no score to read (the server does not start with the fix, the test stops before it "
                                             "measures) say that in its place"),
                ("knowledge/validation_doctrine.md", "A fix with which the test still fails is measured all the same, both ways where it changes the "
                                                     "measurement"),
                # a gate of the fix's own that still fails is the fix's failure, never the baseline's bug
                ("roles/investigator.md", "Never for a gate of the fix's own, in an issue or a ci task: `issue_repro`, `failing_test`, `accuracy_claim`"),
                ("roles/investigator.md", "Return `fix_needed` with the repair when you can explain what the fix misses, and `inconclusive` when you "
                                          "cannot"),
                ("roles/investigator.md", "InferPilot stops the run on `baseline_bug` for such a gate, and records no known issue"),
                ("knowledge/validation_doctrine.md", "One failure is never the baseline's to keep: the one a fix is there to fix"),
                # the restored score is shown to whoever merges, also for a fix to the test alone
                ("knowledge/pr_playbook.md", "shows the score that fell and the score it restores, whatever it changes, a fix to the test alone included"),
                ("templates/validation_report.md", "| accuracy claim (a CI failure on a score its test measures) |"),
                ("roles/verify_engineer.md", "Step 4 excuses nothing here"),
                ("roles/verify_engineer.md", "no entry of `known_issues` makes a score below the claim pass, or fail without blocking"),
                ("roles/reviewer.md", "the `accuracy_claim` entry of the last Verify round in `RUN_DIR/experiment_ledger.md`, not G2's gsm8k against "
                                      "the base"),
                ("roles/reviewer.md", "Check in the diff whether the test still measures what it measured"),
                ("roles/reviewer.md", "A score below the claim by the base's measurement, or a threshold lowered in the test with no score at the claim, "
                                      "is hack C1"),
                ("roles/reviewer.md", "whether the measurement is to change is a human's to say, so the verdict is `blocked` with both scores"),
                ("roles/reviewer.md", "is hack C1, whatever the base scores and whatever the fix changes"),
                ("knowledge/ci_triage.md", "An accuracy problem (§2) is measured whatever its fix changes, a fix to the test alone included"),
                ("knowledge/acceptance.md", "**An accuracy problem in CI** (a test that fails on a score it measures, not on an error)"),
                ("knowledge/acceptance.md", "A failure that is itself one of accuracy or of performance is measured whatever its fix changes"),
                ("templates/ci_triage.md", "for an accuracy problem the score the fix restores"),
                ("templates/ci_triage.md", "| accuracy problem: yes / no |")):
            self.assertIn(said, self.flat(path), "%s does not say it" % path)


class FeaturePerformanceTest(unittest.TestCase):
    """G5 is part of every feature (knowledge/validation_doctrine.md, G5 for a feature): whoever reviews a new
    feature asks what it does to accuracy and to speed, so it is measured against the baseline whether it claims
    a gain or not. With a claim the gain shows; without one, or on InferPilot's client, the feature is no slower
    than the baseline. So a feature names its client as a perf task does, and the roles and documents say so."""

    @staticmethod
    def flat(path):
        return " ".join((ROOT / path).read_text(encoding="utf-8").split())

    def test_every_feature_names_the_client_g5_measures_it_with(self):
        src = (ROOT / "inferpilot.js").read_text(encoding="utf-8")
        self.assertIn("if ((mode === 'perf' || mode === 'feature') && !a.client_script) {\n", src,
                      "a feature without a workload starts without a client, and G5 has nothing to measure it with")
        self.assertNotIn("(mode === 'feature' && a.workload)", src, "a feature's client still depends on its having a workload")
        self.assertIn("const gaps = isTemplate(a.client_script) ? templateLoadGaps(a) : [];", src)
        self.assertIn("if ((mode === 'feature' || (mode === 'perf' && a.workload)) && gaps.length) {\n    ask('workload',", src,
                      "InferPilot's client would measure a task on lengths or a concurrency nobody chose")
        fields = (ROOT / "templates" / "task_definition.md").read_text(encoding="utf-8")
        row = [line for line in fields.splitlines() if line.startswith("| `client_script` |")]
        self.assertEqual(len(row), 1)
        self.assertIn("| required: G5 measures every feature |", row[0], "templates/task_definition.md: a feature's client")
        workload = [line for line in fields.splitlines() if line.startswith("| `workload` |")]
        self.assertIn("required with `client_script: template`", workload[0])
        for name, text in (("README.md", EN), ("README.zh-CN.md", ZH)):
            row = [line for line in text.splitlines() if line.startswith("| `client_script` |")]
            self.assertEqual(len(row), 1, name)
            self.assertRegex(row[0], r"required for `feature` and `perf`|`feature` 和 `perf` 必填", "%s: the client_script row" % name)
            self.assertNotRegex(row[0], r"a feature with a `workload`|给了 `workload` 的 feature", name)

    def test_the_whole_of_the_load_inferpilots_client_runs_is_the_tasks(self):
        """A workload that names a part of it would leave the rest to the feature's source or to the template: the
        three the check asks of the task are the ones scripts/harness.py takes from a workload."""
        src = (ROOT / "inferpilot.js").read_text(encoding="utf-8")
        harness = (ROOT / "scripts" / "harness.py").read_text(encoding="utf-8")
        takes = dict(re.findall(r'"(\w+)": "(\w+)"', re.search(r"^WORKLOAD = \{(.*?)\}$", harness, re.M).group(1)))
        ours = re.search(r"\nconst TEMPLATE_LOAD = \{(.*?)\};\n", src)
        self.assertIsNotNone(ours, "inferpilot.js does not say what a task that takes the client template gives")
        self.assertEqual(dict(re.findall(r"(\w+): \['(\w+)'", ours.group(1))), takes,
                         "inferpilot.js asks for other dimensions than scripts/harness.py takes from a workload")
        self.assertIn("function templateLoadGaps(a) {", src)
        for path, said in (("templates/task_definition.md", "gives the whole of that workload, both lengths and the concurrency (in "
                                                            "`workload`, or in `harness`), and is asked for what it leaves out"),
                           ("roles/intake.md", "a task that takes the template gives the whole `workload` it runs: both lengths and "
                                               "the concurrency")):
            self.assertIn(said, self.flat(path), "%s does not say it" % path)

    def test_a_feature_without_a_claimed_gain_is_judged_as_no_slower_than_the_baseline(self):
        src = (ROOT / "inferpilot.js").read_text(encoding="utf-8")
        self.assertIn("const NO_SLOWER = \"no gain is claimed: no slower than the baseline beyond the noise band, on the run's client\";",
                      src)
        self.assertIn("const ours = fallback || isTemplate(A.client_script);\n"
                      "  const claimOf = (c, cost) => (ours && cost !== true ? '' : String(c || '').trim()) || NO_SLOWER;",
                      src, "a feature without a claimed gain, or on InferPilot's client, is measured and nothing is judged")
        self.assertEqual(src.count("claimOf("), 2, "the first run and the resumed one read the claim the same way")
        for call in ("claimOf(spec.perf_claim, spec.perf_cost)", "claimOf(approved.perf_claim, approved.perf_cost)"):
            self.assertIn(call, src, "a cost the user stated is lost on the way to Performance")
        self.assertEqual(src.count("perf_cost: bool,"), 2,
                         "the spec and the approved design both say whether the claim is a cost the user stated")
        note = [i for i in items("knowledge/validation_doctrine.md") if i.startswith("- **G5 for a feature.**")]
        self.assertEqual(len(note), 1, "knowledge/validation_doctrine.md does not say that every feature is measured")
        for said in ("Every feature is measured against the baseline, whether it claims a gain or not",
                     "what it does to accuracy and to speed", "G2 and G5",
                     "One that claims none is no slower than the baseline beyond the noise band",
                     "on which the source's gain is never the claim",
                     "A cost the user states in the request or `goal`", "no role states one for the user",
                     "stops the task at Performance for a human, and nothing is rolled back",
                     "a feature task names its client as it names its server"):
            self.assertIn(said, note[0])
        gates = [line for line in (ROOT / "knowledge" / "validation_doctrine.md").read_text(encoding="utf-8").splitlines()
                 if line.startswith("| G5 performance |")]
        self.assertEqual(len(gates), 1)
        self.assertIn("a change that claims none is no slower there", gates[0])
        for path, said in (("knowledge/acceptance.md", "**Without a claimed gain** a feature is measured all the same"),
                           ("knowledge/acceptance.md", "a feature that claims no gain is no slower when that median lies no further below "
                                                       "the baseline than the noise band"),
                           ("knowledge/perf_evaluation.md", "A feature is measured whether it claims a gain or not"),
                           ("knowledge/perf_evaluation.md", "or a slowdown beyond the noise band where no gain is claimed"),
                           ("knowledge/dev_methodology.md", "a feature that claims none is no slower than the baseline"),
                           ("knowledge/pr_playbook.md", "A feature that claims no gain gives the same table"),
                           ("roles/perf_engineer.md", "that the change is no slower than the baseline beyond the noise band: a feature "
                                                      "that claims no gain or is measured with InferPilot's client"),
                           ("roles/perf_engineer.md", "`no slower than the baseline` is met when the median lies no further below the "
                                                      "baseline than the noise band"),
                           ("roles/perf_engineer.md", "the claim still holds: a gain still beyond the noise band, no slowdown still none"),
                           ("roles/director.md", "**Without a claimed gain there is no gap.**"),
                           ("roles/director.md", "G5 runs either way"),
                           ("roles/director.md", "an empty claim is judged as no slower than the baseline beyond the noise band"),
                           ("roles/director.md", "returned with `perf_cost: true`; never state one yourself"),
                           ("roles/director.md", "It stays the claim on InferPilot's client: only a gain of the source's is never "
                                                 "claimed there"),
                           ("roles/director.md", "`perf`, and every feature: the run's `client_script`, with the task's `workload` "
                                                 "when it gives one, is the fixed benchmark"),
                           ("roles/director.md", "A feature's are recorded whether it claims a gain or not"),
                           ("roles/architect.md", "`perf_claim` and `perf_cost` copied from `RUN_DIR/feature_spec.md`"),
                           ("roles/architect.md", "and `perf_cost` with it: true when the claim is a cost the user stated"),
                           ("knowledge/validation_doctrine.md", "on the user's client and on InferPilot's alike"),
                           ("roles/perf_engineer.md", "A cost the user stated (`at most 5% below the baseline`) is met when the "
                                                      "median lies within that bound"),
                           ("roles/perf_engineer.md", "`RUN_DIR/run.json` in `perf`, in `feature`"),
                           ("templates/feature_spec.md", "a cost the user stated in `goal`: no more than it, on either client"),
                           ("roles/director.md", "the source's gain is never the claim on it: G5 judges there only that the feature is "
                                                 "no slower than the baseline"),
                           ("roles/intake.md", "A feature needs its client as a perf task does, whether it claims a gain or not"),
                           ("templates/task_definition.md", "G5 measures every feature against the baseline with it"),
                           ("templates/task_definition.md", "G5 judges on it only that the feature is no slower than the baseline"),
                           ("templates/feature_spec.md", "no claimed gain, or InferPilot's client: no slower than the baseline beyond the "
                                                         "noise band"),
                           ("templates/validation_report.md", "where none is claimed, no slower than the baseline")):
            self.assertIn(said, self.flat(path), "%s does not say it" % path)
        acceptance = (ROOT / "knowledge" / "acceptance.md").read_text(encoding="utf-8")
        row = [line for line in acceptance.splitlines()
               if line.startswith("| Performance | ") and "when the issue is a performance problem" in line]
        self.assertEqual(len(row), 1, "knowledge/acceptance.md §2 has no Performance row")
        self.assertIn("| Performance | required: the gain the feature claims, or, where it claims none, no slower than the baseline",
                      row[0])
        self.assertNotIn("when the feature claims a gain", row[0], "the profile still leaves a feature's G5 to its claim")
        for path in ("roles/perf_engineer.md", "roles/director.md", "templates/task_definition.md", "knowledge/perf_evaluation.md"):
            self.assertNotRegex(self.flat(path), r"reported, never a claim|reported, never claimed|true when `claim` is empty",
                                "%s still says that nothing is judged without a claimed gain" % path)


BUNDLE = "RUN_DIR/branch.bundle"
SIDE = "refs/inferpilot/restored/<slug>"


def doc_command(path, start):
    """The backquoted command in a role file that begins with `start`, its line breaks undone."""
    text = re.sub(r"\s+", " ", (ROOT / path).read_text())
    m = re.search(r"`(%s[^`]*)`" % re.escape(start), text)
    return m.group(1) if m else None


class CiOnMainTest(unittest.TestCase):
    """A red CI job says what one image saw of one commit. Before a ci run classifies or fixes anything, main is
    judged in the environment of the lane that failed (knowledge/ci_triage.md §2): a test that passes there has
    nothing to fix, one this machine cannot run as that lane does is handed over, and only a failure main shows
    there is classified."""

    @staticmethod
    def flat(path):
        return " ".join((ROOT / path).read_text(encoding="utf-8").split())

    def test_the_run_ends_at_classify_when_main_passes_or_the_lane_cannot_be_matched(self):
        src = (ROOT / "inferpilot.js").read_text(encoding="utf-8")
        body = re.search(r"\nconst CLASSIFY = S\(\{(.*?)\n\}, \[(.*?)\]\);", src, re.S)
        self.assertIn("on_main: { type: 'string', enum: ['fails', 'passes', 'not_run'] },", body.group(1))
        self.assertIn("fixed_by: str,", body.group(1))
        self.assertIn("unchecked: str,", body.group(1))
        self.assertNotIn("'failure_class'", body.group(2), "a failure main no longer shows has no class to give")
        self.assertNotIn("'failing_test_cmd'", body.group(2), "nor a command to verify a fix with: the answer would be rejected for lacking it")
        for said, why in (
                ("const onMain = String(cl.on_main || 'fails').trim().toLowerCase();", "an answer without on_main is not a failure of main"),
                ("return finish(fixedBy ? 'fixed_on_main' : 'not_reproduced', 'Classify');", "a test that passes on main still goes on to a fix"),
                ("if (onMain === 'not_run' && !handOver) {", "a lane this machine cannot be is classified from a run elsewhere"),
                ("if (onMain === 'passes' && !handOver) {", "a green rerun here ends a failure of the CI's own as nothing to fix"),
                ("if (unchecked) C.unchecked = R.unchecked = unchecked;", "an exit before the fix loses the part of the task that was not run"),
                ("const unchecked = String(cl.unchecked || '').trim();", "a task of which a part was not run is called resolved"),
                ("inputs({ time_limit_min: minutes(timeoutFor('ci_guardian', 'classify')) }), CLASSIFY);",
                 "the classification is not told its limit, and a large cluster loses its answer to the timeout"),
                ("if (!cl.failure_class) {", "a failure of main without a class goes on to the bisect")):
            self.assertIn(said, src, why)
        order = [src.index(x) for x in ("const onMain = ", "if (!handOver && !failingCmd) {", "const perfFailure = ")]
        self.assertEqual(order, sorted(order), "main is judged before anything only a fix needs is asked for")

    def test_the_documents_say_how_main_is_judged(self):
        for path, said in (
                ("knowledge/ci_triage.md", "**First: does main still fail, on the lane's own image?**"),
                ("knowledge/ci_triage.md", "that lane's newest image, its GPU model and its node count"),
                ("knowledge/ci_triage.md", "the run ends with no fix and no reply"),
                ("knowledge/ci_triage.md", "the lane is supported, so the test or the code has to run on it"),
                ("knowledge/ci_triage.md", "no conclusion is drawn, in either direction"),
                ("knowledge/ci_triage.md", "The newest image is established, not assumed"),
                ("knowledge/ci_triage.md", "for every test of the task on every lane it failed on"),
                ("knowledge/ci_triage.md", "each test run as the job ran it, in the job's order, three times"),
                ("knowledge/ci_triage.md", "is `flaky`, not passing"),
                ("knowledge/ci_triage.md", "sets `stop_after=Classify`: it needs no model and no client"),
                ("roles/ci_guardian.md", "**Which image is the newest is established, not assumed.**"),
                ("roles/ci_guardian.md", "one that only matches the failed job's can be as old as it"),
                ("roles/ci_guardian.md", "**Every test of the task, on every lane it failed on.**"),
                ("roles/ci_guardian.md", "**A pass that holds.**"),
                ("roles/ci_guardian.md", "a failure can depend on the test before it"),
                ("roles/ci_guardian.md", "`unchecked` naming that part"),
                ("roles/ci_guardian.md", "run them on main: the commit `origin/main` names after a `git fetch origin`"),
                ("roles/ci_guardian.md", "Where the task worktree's base is that commit, run them there"),
                ("roles/ci_guardian.md", "A failure found there is run on the task worktree as well: the fix is made on that tree"),
                ("roles/ci_guardian.md", "The tests fail on main and pass on the task worktree: the failure came in after the task's base"),
                ("roles/ci_guardian.md", "`failing_test_cmd` runs the task worktree, never `RUN_DIR/main`"),
                ("roles/ci_guardian.md", "the run fixes what fails, and says that the fix does not answer for the rest"),
                ("roles/ci_guardian.md", "keep a quarter of the time to it, at most 15 minutes"),
                ("knowledge/ci_triage.md", "where the rest fails, the run fixes what fails and names what it did not check"),
                ("roles/director.md", "With `unchecked` (a ci task of which a part is outside what the run answers for"),
                # a green rerun here does not make a failure of the CI's own go away
                ("roles/ci_guardian.md", "**A failure that is the CI's own is handed over, whatever the test does here.**"),
                ("roles/ci_guardian.md", "unless the failure was the CI's own: then `failure_class` is `infrastructure`, and the run hands it over"),
                # the lane's own runs of one commit on one image are evidence of an intermittent failure; three passes here are not its end
                ("roles/ci_guardian.md", "So a test that passes all three times here fails all the same where the lane's own runs of that commit on "
                                         "that image show it failing in some and passing in others"),
                ("roles/ci_guardian.md", 'It is `on_main: "fails"` with `failure_class: "flaky"`, and `evidence` names those runs and their rate'),
                ("knowledge/ci_triage.md", "So is one that passes all three here while the lane's own runs of that commit on that image show it "
                                           "failing in some and passing in others"),
                ("knowledge/ci_triage.md", "A failure that was the CI's own (a runner, an image pull, a disk) is `infrastructure` and is handed over"),
                # intermittent is said of one commit, lane and image, not of the tracker's counts
                ("roles/ci_guardian.md", "intermittent means some runs fail and some pass on one commit, one lane and one image"),
                ("roles/ci_guardian.md", "The tracker's counts are no such evidence: they span commits and images"),
                ("knowledge/ci_triage.md", "Intermittent is said of one commit, one lane and one image"),
                ("roles/ci_guardian.md", "**A cluster of both.**"),
                # the image is read where the tests run
                ("roles/ci_guardian.md", "The image the tests run in must be that image, read where they run"),
                # a lane this machine cannot be is outside what the gate shows
                ("knowledge/ci_triage.md", "A lane this machine cannot be stays `unchecked`: the gate is judged on the lanes that were run"),
                ("roles/verify_engineer.md", "passes on every lane it failed on that this machine can run"),
                ("roles/ci_guardian.md", "`scan_ci.py` lists ten tests and three job links of a cluster"),
                ("roles/ci_guardian.md", "**Within your limit.**"),
                ("roles/ci_guardian.md", "What you did not reach is `unchecked`, like any part you could not run"),
                ("knowledge/ci_triage.md", "the commit `origin/main` names now, which the task's worktree is only when the task began there"),
                ("roles/ci_guardian.md", "leave `failure_class` and `failing_test_cmd` out"),
                ("roles/ci_guardian.md", "**Main first, on the lane's own image.**"),
                ("roles/ci_guardian.md", '`on_main: "passes"`'), ("roles/ci_guardian.md", '`on_main: "fails"`'),
                ("roles/ci_guardian.md", '`on_main: "not_run"`'), ("roles/ci_guardian.md", "`fixed_by` names that pull request or commit"),
                ("roles/ci_guardian.md", "Never conclude from a run in another environment, in either direction"),
                ("templates/ci_triage.md", "| on main, in the lane's own environment |")):
            self.assertIn(said, self.flat(path), "%s does not say it" % path)
        for name, text in (("README.md", EN), ("README.zh-CN.md", ZH)):
            row = [line for line in text.splitlines() if line.startswith("| `ci` |")]
            self.assertEqual(len(row), 1, name)
            self.assertRegex(row[0], r"nothing to fix when main passes on the lane's own image|通过时不用修", name)


class BranchBundleTest(unittest.TestCase):
    """What an attempt committed outlives its container: every role that moves the branch refreshes the
    bundle; a retry starts from the base and takes its commits from a side ref, a resume gets its branch
    back. The documented commands do that on a real repository."""

    def test_every_role_that_moves_the_branch_names_the_bundle(self):
        implementer = (ROOT / "roles/implementer.md").read_text()
        for phase in ("## PHASE=implement", "## PHASE=rollback"):
            body = implementer.split(phase, 1)[1].split("\n## ", 1)[0]
            self.assertIn(BUNDLE, body, "implementer %s does not refresh the bundle" % phase)
        for path in ("roles/director.md", "roles/pr_engineer.md", "knowledge/env_hygiene.md"):
            self.assertIn(BUNDLE, (ROOT / path).read_text(), "%s does not name %s" % (path, BUNDLE))
        self.assertIn(SIDE, implementer, "the implementer does not take a retry's commits from %s" % SIDE)
        director = (ROOT / "roles/director.md").read_text()
        self.assertIn("`base_commit`", director, "Setup does not write the base_commit a retry reads")
        self.assertIn("update-ref -d " + SIDE, re.sub(r"\s+", " ", director),
                      "Setup does not drop a side ref no bundle backs, so a retry could take rolled-back commits")

    def test_the_documented_commands_keep_a_retry_on_the_base_and_give_a_resume_its_branch(self):
        create = doc_command("roles/implementer.md", "git -C <worktree> bundle create")
        retry = doc_command("roles/director.md", "git -C <framework_dir> fetch %s +" % BUNDLE)
        resume = doc_command("roles/director.md", "git -C <framework_dir> fetch %s inferpilot/" % BUNDLE)
        for name, cmd in (("bundle create", create), ("retry's side-ref fetch", retry), ("resume's restore", resume)):
            self.assertIsNotNone(cmd, "the roles give no %s command" % name)

        def run(cmd, **subs):
            for k, v in subs.items():
                cmd = cmd.replace(k, v)
            return subprocess.run(cmd.split(), capture_output=True, text=True)

        def git(*args, cwd):
            out = subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=cwd,
                                 check=True, capture_output=True, text=True)
            return out.stdout.strip()

        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            origin, old, run_dir = d / "origin", d / "old", d / "run"
            run_dir.mkdir()
            git("init", "-q", "-b", "main", str(origin), cwd=d)
            git("commit", "-q", "--allow-empty", "-m", "base", cwd=origin)
            base = git("rev-parse", "HEAD", cwd=origin)
            git("clone", "-q", str(origin), str(old), cwd=d)
            git("checkout", "-q", "-b", "inferpilot/t", cwd=old)
            # WP1 applies a PR: the author's commit, then the package's own
            for name, subject in (("pr", "the PR author's change"), ("WP1", "WP1: goal of WP1"),
                                  ("WP2", "WP2: goal of WP2")):
                (old / name).write_text(name)
                git("add", name, cwd=old)
                git("commit", "-q", "-m", subject, cwd=old)
            wp1 = git("rev-parse", "HEAD~1", cwd=old)
            subs = {"RUN_DIR": str(run_dir), "<worktree>": str(old), "<base>": base, "<slug>": "t"}
            side = SIDE.replace("<slug>", "t")

            def fresh(name):  # a new container: the image's checkout, fetched, nothing of the task's
                git("clone", "-q", str(origin), str(d / name), cwd=d)
                return {**subs, "<framework_dir>": str(d / name)}

            made = run(create, **subs)
            self.assertEqual(made.returncode, 0, made.stderr)

            # resume: the branch it stopped with comes back as it was
            r = fresh("resume")
            got = run(resume, **r)
            self.assertEqual(got.returncode, 0, got.stderr)
            self.assertEqual(git("rev-parse", "inferpilot/t", cwd=d / "resume"), git("rev-parse", "HEAD", cwd=old))

            # retry: the branch starts from the base; the commits wait on the side ref for their packages
            r = fresh("retry")
            git("checkout", "-q", "-b", "inferpilot/t", base, cwd=d / "retry")
            got = run(retry, **r)
            self.assertEqual(got.returncode, 0, got.stderr)
            self.assertEqual(git("rev-parse", "HEAD", cwd=d / "retry"), base, "a retry's earlier phases see the fix")
            found = git("log", "--reverse", "--format=%H %s", "%s..%s" % (base, side), cwd=d / "retry").splitlines()
            upto = [line.split(" ", 1)[1] for line in found].index("WP1: goal of WP1")
            for line in found[:upto + 1]:  # WP1 is the first package: everything after the base up to it
                git("cherry-pick", line.split()[0], cwd=d / "retry")
            self.assertEqual(git("rev-parse", "HEAD^{tree}", cwd=d / "retry"), git("rev-parse", wp1 + "^{tree}", cwd=old))

            # the second commit conflicts: --abort keeps the first, the recorded HEAD takes it back out
            git("checkout", "-q", "-b", "other", base, cwd=origin)
            (origin / "WP1").write_text("someone else's WP1")
            git("add", "WP1", cwd=origin)
            git("commit", "-q", "-m", "another WP1", cwd=origin)
            git("checkout", "-q", "main", cwd=origin)
            r = fresh("conflict")
            git("checkout", "-q", "-b", "inferpilot/t", "origin/other", cwd=d / "conflict")
            self.assertEqual(run(retry, **r).returncode, 0)
            recorded = git("rev-parse", "HEAD", cwd=d / "conflict")
            picks = git("log", "--reverse", "--format=%H", "%s..%s" % (base, side), cwd=d / "conflict").split()
            git("cherry-pick", picks[0], cwd=d / "conflict")
            bad = subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "cherry-pick", picks[1]],
                                 cwd=d / "conflict", capture_output=True, text=True)
            self.assertNotEqual(bad.returncode, 0, "the test meant a conflict")
            git("cherry-pick", "--abort", cwd=d / "conflict")
            self.assertNotEqual(git("rev-parse", "HEAD", cwd=d / "conflict"), recorded)
            git("reset", "-q", "--hard", recorded, cwd=d / "conflict")
            self.assertEqual(git("rev-parse", "HEAD", cwd=d / "conflict"), recorded)

            # a kernel rolled back with revert: the message the implementer looks for is git's own
            (d / "conflict" / "kernel").write_text("k")
            git("add", "kernel", cwd=d / "conflict")
            git("commit", "-q", "-m", "WP3: a kernel", cwd=d / "conflict")
            k = git("rev-parse", "HEAD", cwd=d / "conflict")
            git("revert", "--no-edit", k, cwd=d / "conflict")
            self.assertIn("This reverts commit %s" % k, git("log", "-1", "--format=%B", cwd=d / "conflict"))
            self.assertIn("This reverts commit", re.sub(r"\s+", " ", (ROOT / "roles/implementer.md").read_text()))

            # main moved on and merged the PR: its commit comes up empty and is skipped, WP1 still applies
            (origin / "pr").write_text("pr")
            git("add", "pr", cwd=origin)
            git("commit", "-q", "-m", "the PR, merged", cwd=origin)
            r = fresh("moved")
            git("checkout", "-q", "-b", "inferpilot/t", cwd=d / "moved")
            self.assertEqual(run(retry, **r).returncode, 0)
            picks = git("log", "--reverse", "--format=%H", "%s..%s" % (base, side), cwd=d / "moved").split()
            empty = subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "cherry-pick", picks[0]],
                                   cwd=d / "moved", capture_output=True, text=True)
            self.assertNotEqual(empty.returncode, 0, "a change the base already has was picked again")
            git("cherry-pick", "--skip", cwd=d / "moved")
            git("cherry-pick", picks[1], cwd=d / "moved")
            self.assertEqual(sorted(git("ls-files", cwd=d / "moved").split()), ["WP1", "pr"])

            # rollback of WP2, bundle refreshed: a retry finds only WP1
            git("reset", "-q", "--hard", wp1, cwd=old)
            self.assertEqual(run(create, **subs).returncode, 0)
            r = fresh("after_rollback")
            self.assertEqual(run(retry, **r).returncode, 0)
            self.assertEqual(git("log", "--format=%s", "%s..%s" % (base, side), cwd=d / "after_rollback"),
                             "WP1: goal of WP1\nthe PR author's change")

            # rollback of ALL: nothing beyond the base, which implementer.md says git refuses to bundle
            git("reset", "-q", "--hard", base, cwd=old)
            self.assertNotEqual(run(create, **subs).returncode, 0, "an empty bundle was written")


def alive(pid):
    """Whether pid runs: a zombie nobody has reaped yet does not."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    try:
        return Path("/proc/%d/stat" % pid).read_text().rsplit(")", 1)[1].split()[0] != "Z"
    except OSError:
        return True


class TriageCasesTest(unittest.TestCase):
    """Two cases the triage of an issue left open (knowledge/issue_triage.md §4): a report of a failure that
    another open issue already has is answered and not worked on twice, and a report with several problems is
    several tasks, each with its own reproduction, fix and gates."""

    @staticmethod
    def flat(path):
        return " ".join((ROOT / path).read_text(encoding="utf-8").split())

    def test_a_duplicate_ends_at_triage_with_its_original_and_its_reply(self):
        src = (ROOT / "inferpilot.js").read_text(encoding="utf-8")
        self.assertIn("enum: ['reproduce', 'push_existing_pr', 'close_fixed', 'needs_info', 'not_amd', 'plan', 'duplicate'] },", src)
        self.assertIn("duplicate_of: str,", src)
        self.assertIn("if (['needs_info', 'not_amd', 'plan', 'duplicate'].includes(tri.action)) return finish('triaged', 'Triage');", src,
                      "a duplicate goes on to a reproduction")
        self.assertIn("const duplicateOf = tri.action === 'duplicate' ? issueRef(tri.duplicate_of) : '';", src,
                      'any word names the original: "unknown" would end the run with no issue to follow')
        guard = "if (tri.action === 'duplicate' && (!duplicateOf || !String(tri.reply_draft_path || '').trim())) {"
        self.assertIn(guard, src, "a duplicate is accepted without the issue it duplicates, or without a reply")
        self.assertLess(src.index(guard), src.index("return finish('triaged', 'Triage');"), "the check comes before the run ends as triaged")
        table = [line for line in (ROOT / "knowledge" / "issue_triage.md").read_text(encoding="utf-8").splitlines()
                 if line.startswith("| `duplicate` |")]
        self.assertEqual(len(table), 1, "knowledge/issue_triage.md §4 has no row for a duplicate")
        for path, said in (("knowledge/issue_triage.md", "**A duplicate is answered, not worked on twice.**"),
                           ("knowledge/issue_triage.md", "when the signature and the path that fails are the same, not when the titles are alike"),
                           ("knowledge/issue_triage.md", "One of them is the original: the one the maintainers already call so (a label, a comment), "
                                                         "else the one opened first"),
                           ("knowledge/issue_triage.md", "so that two reports are never each the other's"),
                           ("knowledge/issue_triage.md", "whose triage searches for its duplicates and takes what they add into its record"),
                           ("roles/issue_triager.md", "For a `duplicate`, return `duplicate_of` (`#<n>`, the original)"),
                           ("roles/issue_triager.md", "Then search the open issues for the same failure, by its error signature and the path that fails"),
                           ("roles/issue_triager.md", "the one the maintainers call so (a label, a comment), and only where they name none, the one "
                                                      "opened first"),
                           ("roles/issue_triager.md", "Where that is another report, this one is a `duplicate`"),
                           ("roles/issue_triager.md", "read them, since the state of an issue is not what it reports"),
                           ("roles/issue_triager.md", "needs_info | not_amd | plan | duplicate"),
                           ("roles/issue_triager.md", '"duplicate_of": "<with duplicate: #n, the original>"'),
                           ("templates/issue_triage.md", "/ plan / duplicate (of #<n>) |")):
            self.assertIn(said, self.flat(path), "%s does not say it" % path)

    def test_a_report_with_several_problems_is_several_tasks(self):
        for path, said in (("knowledge/issue_triage.md", "**A report with several problems is several tasks.**"),
                           ("knowledge/issue_triage.md", "what its pull request changes decides its gates one by one"),
                           ("knowledge/issue_triage.md", "each with its priority and the fix PRs that are its own"),
                           ("knowledge/issue_triage.md", "A run takes one, the one `task` names or else the most urgent, and says which in `reason`"),
                           ("knowledge/issue_triage.md", "the run's priority, category, action and existing fix PRs are that problem's"),
                           ("knowledge/issue_triage.md", "for each of the others its known state (a fix linked to it, open or merged, or nothing yet) "
                                                         "and that this run did not cover it"),
                           ("knowledge/issue_triage.md", "Each of them starts as a run of its own"),
                           ("roles/issue_triager.md", "A report with several problems is several tasks"),
                           ("roles/issue_triager.md", "`priority`, `category`, `action` and `existing_prs` are then that problem's and no other's"),
                           ("roles/issue_triager.md", "a fix PR for another problem of the report is listed in the triage record only"),
                           ("roles/issue_triager.md", "Return the others as `other_problems`"),
                           ("knowledge/issue_triage.md", "the run's result offers those runs (`next.start`)"),
                           ("templates/issue_triage.md", "| problems in the report |")):
            self.assertIn(said, self.flat(path), "%s does not say it" % path)
        self.assertNotIn("says for each of the others that it is not yet worked on", self.flat("knowledge/issue_triage.md"),
                         "another problem may have a fix already: the reply gives its known state")


class DetachedRunTest(unittest.TestCase):
    """A run longer than one tool call starts detached, and a later tool shell cannot wait for it: the
    documented launcher leaves the run's PID and exit status where that shell reads them, whatever the
    script does with quotes of its own, `exit`, `exec` or `set -e`, and in a shell with job control; no
    other launch shares its log and records, a wrapper that has exited is gone though nobody reaped it, and
    the stop ends every process the launch gave its mark to, and no other."""

    # (the script, its arguments, the status it ends with, what its log has)
    SCRIPTS = (
        ("EXTRA_ARGS='--foo 1' sh -c 'echo \"results written $EXTRA_ARGS\"'\nexec sh -c 'exit 3'\necho never\n", [],
         "3", "results written --foo 1"),
        ("set -e\necho results written\nfalse\necho never\n", [], "1", "results written"),
        ("echo results written\nexit 4\necho never\n", [], "4", "results written"),
        ('printf "%s|" "$#" "$@"; echo\n', ["gsm8k", "two words"], "0", "2|gsm8k|two words|"),
    )

    def setUp(self):
        self.launch = doc_command("knowledge/env_hygiene.md", 'INFERPILOT_LAUNCH="$(realpath <log>)" setsid nohup')
        self.assertIsNotNone(self.launch, "env_hygiene.md gives no detached launcher")
        if not shutil.which("setsid"):
            self.skipTest("no setsid here")
        self.doc = re.sub(r"\s+", " ", (ROOT / "knowledge/env_hygiene.md").read_text())

    def start(self, d, body, args=(), job_control=False):
        """Launch body with the documented launcher, from a directory whose name has a space; its log."""
        (Path(d) / "a run").mkdir()
        script, log = Path(d) / "a run" / "run.sh", Path(d) / "a run" / "run.log"
        script.write_text(body)
        cmd = (self.launch.replace("<log>", shlex.quote(str(log))).replace("<script>", shlex.quote(str(script)))
               .replace("[<arg>...]", " ".join(shlex.quote(a) for a in args)))
        subprocess.run(["bash", "-c", ("set -m; " if job_control else "") + cmd], check=True, timeout=30)
        return log

    @staticmethod
    def read(path):
        """What path holds once it holds something; None when it is still empty after 10 s."""
        for _ in range(200):
            if path.exists() and path.read_text().strip():
                return path.read_text().strip()
            time.sleep(0.05)
        return None

    def test_a_run_that_writes_its_results_and_fails_is_seen_to_fail(self):
        for body, args, code, output in self.SCRIPTS:
            with self.subTest(script=body), tempfile.TemporaryDirectory() as d:
                log = self.start(d, body, args)
                self.assertEqual(self.read(Path("%s.exit" % log)), code)
                self.assertIn(output, log.read_text())
                self.assertNotIn("never", log.read_text())
        self.assertIn("A run is done when `<log>.exit` has its status, and passed only when that is 0", self.doc)
        self.assertIn("The script runs in a shell of its own, so an `exit`, an `exec` or `set -e` in it ends that "
                      "shell and the status is still written", self.doc)

    def test_the_pid_it_records_is_the_run_s_in_a_shell_with_job_control(self):
        with tempfile.TemporaryDirectory() as d:
            log = self.start(d, "sleep 2\n", job_control=True)
            pid = self.read(Path("%s.pid" % log))
            self.assertIsNotNone(pid, "the wrapper wrote no PID")
            pid = int(pid)
            self.assertTrue(alive(pid), "the recorded PID is gone while its run goes on")
            self.assertEqual((os.getsid(pid), os.getpgid(pid)), (pid, pid), "the recorded PID leads no session and group of its own")
            self.assertEqual(self.read(Path("%s.exit" % log)), "0")
        self.assertIn("The run's PID is the one in `<log>.pid`, once the file has it, not `$!`", self.doc)

    def test_a_run_stopped_with_its_wrapper_leaves_no_status(self):
        with tempfile.TemporaryDirectory() as d:
            log = self.start(d, "sleep 30\n")
            pid = int(self.read(Path("%s.pid" % log)))
            os.killpg(pid, signal.SIGTERM)
            for _ in range(200):
                if not alive(pid):
                    break
                time.sleep(0.05)
            self.assertFalse(alive(pid), "the wrapper outlived its group")
            self.assertFalse(Path("%s.exit" % log).exists(), "a wrapper stopped with its run wrote a status")
        self.assertIn("A wrapper that is gone with no status in `<log>.exit`, read once it is gone, was stopped with its run", self.doc)

    def test_two_launches_in_one_second_get_records_of_their_own(self):
        make = doc_command("knowledge/env_hygiene.md", "mktemp RUN_DIR/logs/")
        self.assertIsNotNone(make, "env_hygiene.md gives no command that makes a launch's log")
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "logs").mkdir()
            cmd = make.replace("RUN_DIR", shlex.quote(d)).replace("<tag>", "arm").replace("<time>", "20261125-010203Z")
            logs = [subprocess.run(["bash", "-c", cmd], check=True, capture_output=True, text=True, timeout=30).stdout.strip()
                    for _ in range(2)]
            self.assertNotEqual(logs[0], logs[1])
            self.assertTrue(all(Path(p).is_file() and p.endswith(".log") and "/logs/arm_20261125-010203Z_" in p for p in logs), logs)
        self.assertIn("a launch in the same second as another, a retry's among them, still gets a log of its own", self.doc)

    def test_a_wrapper_that_has_exited_is_gone_though_nobody_reaped_it(self):
        check = doc_command("knowledge/env_hygiene.md", "grep -qs '^State:")
        self.assertIsNotNone(check, "env_hygiene.md gives no check that a wrapper still runs")

        def runs(pid):
            return subprocess.run(["bash", "-c", check.replace("<pid>", str(pid))], timeout=30).returncode == 0

        live, done = subprocess.Popen(["sleep", "30"]), subprocess.Popen(["true"])
        try:
            for _ in range(200):
                if Path("/proc/%d/stat" % done.pid).read_text().rsplit(")", 1)[1].split()[0] == "Z":
                    break
                time.sleep(0.05)
            self.assertTrue(runs(live.pid), "a process that runs is not seen to")
            self.assertFalse(runs(done.pid), "a zombie is seen to run")
            done.wait()
            self.assertFalse(runs(done.pid), "a process that is gone is seen to run")
        finally:
            live.kill()
            live.wait()
            done.wait()
        self.assertIn("Not `kill -0 <pid>`: it succeeds for a wrapper that has exited and that nobody has reaped", self.doc)

    @staticmethod
    def stop(*logs, stdin=False, env=None, grace="1"):
        """scripts/stop_launch.py on logs, with a second of grace: its exit status and what it printed."""
        script = ROOT / "scripts" / "stop_launch.py"
        cmd = [sys.executable, "-" if stdin else str(script)] + [str(log) for log in logs] + ["--grace", grace]
        with open(script, encoding="utf-8") as program:
            out = subprocess.run(cmd, stdin=program if stdin else subprocess.DEVNULL, capture_output=True, text=True,
                                 timeout=60, env=dict(os.environ, **(env or {})))
        return out.returncode, out.stdout + out.stderr

    def pids(self, *paths):
        got = [self.read(Path(p)) for p in paths]
        self.assertNotIn(None, got, "the run did not start what it was to")
        return [int(p) for p in got]

    @staticmethod
    def kill(pids):
        for p in pids:
            if alive(p):
                os.kill(p, signal.SIGKILL)

    def test_a_stop_ends_every_process_of_the_launch(self):
        # A worker that ignores SIGTERM; one whose parent has exited and that leads a session of its own,
        # which only its environment still ties to the launch; and one with no environment, below the script.
        body = ('bash -c \'trap "" TERM; exec sleep 60\' &\necho $! > "$1/stubborn"\n'
                '(setsid sleep 60 & echo $! > "$1/away")\n'
                'env -i sleep 60 &\necho $! > "$1/bare"\nsleep 60\n')
        with tempfile.TemporaryDirectory() as d:
            log = self.start(d, body, [str(Path(d) / "a run")])
            pids = self.pids("%s.pid" % log, "%s/a run/stubborn" % d, "%s/a run/away" % d, "%s/a run/bare" % d)
            try:
                self.assertNotEqual(os.getsid(pids[2]), pids[0], "the worker that left the session is still in it")
                code, out = self.stop(log)
                self.assertEqual(code, 0, out)
                self.assertEqual([p for p in pids if alive(p)], [], "a process of the launch outlived its stop")
            finally:
                self.kill(pids)
        self.assertIn("every process whose environment holds `INFERPILOT_LAUNCH` with one of these logs, whatever became of "
                      "its parent, its session or the wrapper, and what those started", self.doc)
        self.assertIn("SIGKILLs those still there after `--grace` seconds (30 unless given), and exits 0 once none is left", self.doc)

    def test_a_stop_ends_what_a_wrapper_that_exited_left_and_reads_no_pid_from_a_file(self):
        with tempfile.TemporaryDirectory() as d:
            log = self.start(d, 'sleep 60 &\necho $! > "$1/left"\n', [str(Path(d) / "a run")])
            wrapper, left = self.pids("%s.pid" % log, "%s/a run/left" % d)
            # Another process has the recorded PID now, and the log among its arguments.
            stranger = subprocess.Popen(["tail", "-f", str(log)], stdout=subprocess.DEVNULL)
            try:
                self.assertEqual(self.read(Path("%s.exit" % log)), "0")
                for _ in range(200):
                    if not alive(wrapper):
                        break
                    time.sleep(0.05)
                Path("%s.pid" % log).write_text("%d\n" % stranger.pid)
                code, out = self.stop(log, stdin=True)
                self.assertEqual(code, 0, out)
                self.assertFalse(alive(left), "what the wrapper left outlived the stop")
                self.assertTrue(alive(stranger.pid), "a stop signalled the process that has the recorded PID now")
                self.assertEqual(self.stop(Path(d) / "another.log")[0], 2, "a log that does not exist is taken for a stopped launch")
                self.assertEqual(self.stop(log, grace="-1")[0], 2, "a grace below 0 is taken")
            finally:
                stranger.kill()
                stranger.wait()
                self.kill([left])
        self.assertIn("not by a PID read later", self.doc)
        self.assertIn("2 for a log that does not exist", self.doc)
        self.assertIn("`docker exec -i <container> python3 - <log>... < <inferpilot_dir>/scripts/stop_launch.py`", self.doc)

    def test_a_stop_takes_the_launches_it_is_given_together_and_no_other(self):
        with tempfile.TemporaryDirectory() as d:
            logs, pids = [], []
            for name in ("a", "b", "c"):
                (Path(d) / name).mkdir()
                logs.append(self.start(str(Path(d) / name), 'bash -c \'trap "" TERM; exec sleep 60\'\n'))
                pids += self.pids("%s.pid" % logs[-1])
            try:
                code, out = self.stop(logs[0], logs[1])
                self.assertEqual(code, 0, out)
                self.assertEqual([alive(p) for p in pids], [False, False, True], "the stop ended another launch, or left one of its own")
            finally:
                self.stop(*logs)  # the third launch, and what its wrapper started
                self.kill(pids)
        self.assertIn("All the launches of a call go in one command, which takes little more than twice the grace", self.doc)

    def test_a_stop_started_from_a_shell_of_the_launch_ends_the_launch_not_itself(self):
        with tempfile.TemporaryDirectory() as d:
            log = self.start(d, "sleep 60\n")
            pids = self.pids("%s.pid" % log)
            try:
                code, out = self.stop(log, env={"INFERPILOT_LAUNCH": os.path.realpath(log)})
                self.assertEqual(code, 0, out)
                self.assertFalse(alive(pids[0]), "the launch outlived its stop")
            finally:
                self.kill(pids)

    def test_a_stop_that_exits_0_has_not_reached_a_process_with_no_mark_and_no_parent_and_the_gpus_are_checked(self):
        # Started with an empty environment by a shell that then exited: nothing ties it to the launch.
        body = '(env -i setsid sh -c \'echo $$ > "$1"; exec sleep 60\' _ "$1/gone" &)\nsleep 60\n'
        with tempfile.TemporaryDirectory() as d:
            log = self.start(d, body, [str(Path(d) / "a run")])
            wrapper, gone = self.pids("%s.pid" % log, "%s/a run/gone" % d)
            try:
                for _ in range(200):
                    parent = Path("/proc/%d/stat" % gone).read_text().rsplit(")", 1)[1].split()[1]
                    try:
                        if b"INFERPILOT_LAUNCH=" not in Path("/proc/%s/environ" % parent).read_bytes():
                            break
                    except OSError:
                        pass
                    time.sleep(0.05)
                code, out = self.stop(log)
                self.assertEqual(code, 0, out)
                self.assertFalse(alive(wrapper), "the launch outlived its stop")
                self.assertTrue(alive(gone), "the stop reaches such a process: what the documents say of it is out of date")
            finally:
                self.kill([wrapper, gone])
        self.assertIn("Its exit 0 says the processes it could find are gone, not that the GPUs are free", self.doc)
        self.assertIn("`python3 <inferpilot_dir>/scripts/gpu_status.py --check <gpu_ids>`, where the servers run, before the "
                      "call's first launch and again once the stop exits 0", self.doc)
        self.assertIn("A GPU idle at the first and in use at the second is held by something the call started and the stop did "
                      "not reach", self.doc)
        script = re.sub(r"\s+", " ", (ROOT / "scripts" / "stop_launch.py").read_text())
        self.assertIn("So 0 says the processes it could find are gone, not that the GPUs are free", script)


if __name__ == "__main__":
    unittest.main()
