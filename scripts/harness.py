#!/usr/bin/env python3
"""InferPilot's templates: the server, performance and accuracy scripts rendered from templates/harness/ for
a run, the server and the client only where the user took them for a script of their own, and the accuracy
script unless the task brings one. Each parameter takes the task's value first, then the one the feature the
task ports sets in its source's own tests (vLLM PR #49406 serves PARD-2 with --max-model-len 4096:
CONTEXT_LENGTH=4096), then, for the server, the model's recipe in the sglang cookbook for this GPU, then the
template's default.

    python3 scripts/harness.py show                 # every parameter: its script, default and meaning
    python3 scripts/harness.py render --out RUN_DIR/scripts --set MODEL_PATH=/models/m --set TP=4 \\
        --workload '{"isl": 8192, "osl": 1024, "conc": 64}' --feature RUN_DIR/feature_params.json \\
        --cookbook RUN_DIR/scripts/cookbook.txt --cookbook-source "docs/cookbook/.../GLM-5.1.mdx, MI355X FP8"

render writes server.sh, perf.sh and accuracy.sh to --out (--scripts picks some; --name NAME writes the one
picked as NAME.sh, with NAME.json beside it), each parameter line holding its value and the kind of its source,
and harness.json: when it was rendered and, per parameter, its script, value and source (task, feature,
cookbook or template) with the words it came from, the cookbook recipe, and accuracy_server_env, what the server of an accuracy run sets beyond server.sh (CONTEXT_LENGTH emptied when
that context cannot hold MAX_TOKENS and a question). The task's values come from --set NAME=VALUE, --task (a
JSON object, the task's harness field) and --workload (its isl, osl and conc); the feature's from --feature,
a JSON object or file of {"NAME": {"value": ..., "source": "<where the source sets it>"}} or of plain values,
which --feature-source describes. --cookbook is the recipe's launch command, as text or a file: its
environment settings and `sglang serve` or `python -m sglang.launch_server` with its flags. The flags of a
parameter line fill that parameter, the model, host and port are the task's and InferPilot's, and the
environment and the other flags go to server.sh's cookbook block as written. A name no template has, or a
value its line cannot hold, is an error (exit 2). A rendered script holds no time and none of those words, so
the same values render the same script: the baseline cache fingerprints its contents.
"""
import argparse
import datetime
import json
import re
import shlex
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = ROOT / "templates" / "harness"
SCRIPTS = ("server", "perf", "accuracy")
BEGIN, END = "# >>> parameters", "# <<< parameters"
COOKBOOK_BEGIN, COOKBOOK_END = "# >>> cookbook", "# <<< cookbook"
LINE = re.compile(r'^(?P<name>[A-Z][A-Z0-9_]*)="\$\{(?P=name)(?P<op>:?-)(?P<default>[^"$`\\}]*)\}"(?P<pad>\s*)'
                  r'# (?P<type>int|float|bool|str|ints|strs); (?P<about>.*)$')
# What a value of each type may be; none may hold what would end or expand its "${NAME:-...}" line.
VALUES = {"int": r"-?\d+", "float": r"-?\d+(\.\d+)?", "bool": r"[01]", "ints": r"\d+( \d+)*",
          "str": r'[^"$`\\}\n]*', "strs": r'[^"$`\\}\n]*'}
WORKLOAD = {"isl": "ISL", "osl": "OSL", "conc": "CONCURRENCY"}
# The flags of a cookbook command that server.sh's parameter lines hold.
COOKBOOK_FLAGS = {"--tp": "TP", "--tp-size": "TP", "--tensor-parallel-size": "TP", "--context-length": "CONTEXT_LENGTH",
                  "--mem-fraction-static": "MEM_FRACTION_STATIC", "--max-running-requests": "MAX_RUNNING_REQUESTS",
                  "--chunked-prefill-size": "CHUNKED_PREFILL_SIZE"}
ENV_WORD = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
# The room a question needs beside its answer in an accuracy run.
QUESTION_ROOM = 2048
INFERPILOT_DIR_LINE = re.compile(r'^INFERPILOT_DIR="\$\{INFERPILOT_DIR:-.*\}"$', re.M)


class HarnessError(Exception):
    pass


def block(lines, begin, end, script):
    """The line numbers of a template's block markers."""
    first = next((i for i, line in enumerate(lines) if line.startswith(begin)), None)
    last = next((i for i, line in enumerate(lines) if line.startswith(end)), None)
    if first is None or last is None or last < first:
        raise HarnessError("templates/harness/%s.sh has no %s block" % (script, begin[6:]))
    return first, last


def parameters(script):
    """The parameter lines of templates/harness/<script>.sh, in order."""
    lines = (TEMPLATES / ("%s.sh" % script)).read_text(encoding="utf-8").splitlines()
    begin, end = block(lines, BEGIN, END, script)
    out = []
    for n in range(begin + 1, end):
        m = LINE.match(lines[n])
        if not m:
            raise HarnessError("templates/harness/%s.sh:%d is not a parameter line" % (script, n + 1))
        out.append(dict(m.groupdict(), script=script, line=n))
    return out


def catalog():
    out = {}
    for script in SCRIPTS:
        for p in parameters(script):
            if p["name"] in out:
                raise HarnessError("%s is a parameter of both %s.sh and %s.sh" % (p["name"], out[p["name"]]["script"], script))
            out[p["name"]] = p
    return out


def value_of(p, raw):
    """raw (from JSON or the command line) as the text of p's line."""
    if isinstance(raw, bool):
        raw = "1" if raw else "0"
    elif isinstance(raw, list):
        raw = " ".join(str(v) for v in raw)
    elif raw is None:
        raw = ""
    text = " ".join(str(raw).split()) if p["type"] in ("ints", "strs") else str(raw)
    if p["type"] == "bool" and text.lower() in ("true", "false"):
        text = "1" if text.lower() == "true" else "0"
    if text == "" and p["op"] == "-":
        return text
    if text == "" or not re.fullmatch(VALUES[p["type"]], text):
        raise HarnessError("%s=%r is not a value of its line (%s; %s)" % (p["name"], raw, p["type"], p["about"]))
    return text


def load(arg, what):
    """A JSON object, given inline or as a file."""
    if arg is None:
        return {}
    path = Path(arg)
    try:
        text = path.read_text(encoding="utf-8") if not arg.lstrip().startswith("{") and path.is_file() else arg
        obj = json.loads(text)
    except (OSError, ValueError) as e:
        raise HarnessError("%s is not a JSON object or a file of one: %s" % (what, e))
    if not isinstance(obj, dict):
        raise HarnessError("%s is not a JSON object" % what)
    return obj


def resolve(task, feature, cookbook=None, scripts=SCRIPTS):
    """The value and source of every parameter of scripts. task: {NAME: value}; feature and cookbook:
    {NAME: (value, words)}."""
    cookbook = cookbook or {}
    cat = catalog()
    for name in sorted(set(task) | set(feature)):
        if name not in cat:
            raise HarnessError("no template has a parameter %s (scripts/harness.py show lists them)" % name)
    out = {}
    for name, p in cat.items():
        if p["script"] not in scripts:
            continue
        if name in task:
            entry = {"value": value_of(p, task[name]), "source": "task"}
        elif name in feature:
            entry = {"value": value_of(p, feature[name][0]), "source": "feature", "why": feature[name][1]}
        elif name in cookbook:
            entry = {"value": value_of(p, cookbook[name][0]), "source": "cookbook", "why": cookbook[name][1]}
        else:
            entry = {"value": p["default"], "source": "template"}
        if entry["value"] == "" and p["op"] == ":-":
            raise HarnessError("%s has no value: give the task's (--set %s=...)" % (name, name))
        out[name] = dict(entry, script=p["script"])
    # accuracy.sh sends no reasoning effort without thinking; the record says what it sends.
    if out.get("ENABLE_THINKING", {}).get("value") == "0" and out.get("REASONING_EFFORT", {}).get("value"):
        out["REASONING_EFFORT"].update(value="", why="thinking is off")
    return out


def task_values(sets, task, workload):
    out = {}
    if workload:
        bad = sorted(set(workload) - set(WORKLOAD))
        if bad:
            raise HarnessError("--workload: %s unknown; it takes %s" % (", ".join(bad), ", ".join(WORKLOAD)))
        out.update({WORKLOAD[k]: v for k, v in workload.items()})
    out.update(task)
    for item in sets:
        name, sep, value = item.partition("=")
        if not sep:
            raise HarnessError("--set takes NAME=VALUE, not %r" % item)
        out[name] = value
    return out


def feature_values(feature, words):
    out = {}
    for name, v in feature.items():
        if isinstance(v, dict):
            if "value" not in v:
                raise HarnessError("--feature: %s has no value" % name)
            out[name] = (v["value"], str(v.get("source") or words))
        else:
            out[name] = (v, words)
    return out


def text_or_file(arg):
    """arg, or the text of the file it names."""
    if arg and "\n" not in arg:
        try:
            if Path(arg).is_file():
                return Path(arg).read_text(encoding="utf-8")
        except OSError:  # a command too long to be a file name
            pass
    return arg


def cookbook_values(text, source):
    """A cookbook recipe's launch command as the values of server.sh's parameter lines ({NAME: (value, words)}),
    and the recipe itself: its source, the model it names, its environment and its other flags, each flag with
    its values."""
    if not text:
        return {}, None
    try:
        words = [w for w in shlex.split(text.replace("\\\n", " "), comments=True) if w != "export"]
    except ValueError as e:
        raise HarnessError("--cookbook cannot be read as a command: %s" % e)
    start = None
    for i, w in enumerate(words):
        if w == "sglang" and words[i + 1:i + 2] == ["serve"]:
            start, skip = i, 2
        elif re.fullmatch(r"python3?", w) and words[i + 1:i + 3] == ["-m", "sglang.launch_server"]:
            start, skip = i, 3
        if start is not None:
            break
    if start is None:
        raise HarnessError("--cookbook holds no sglang launch command (sglang serve, python -m sglang.launch_server)")
    if "docker" in words[:start]:
        raise HarnessError("--cookbook: take the recipe's command that runs sglang itself, not its docker command")
    env = []
    for w in words[:start]:
        if not ENV_WORD.match(w):
            raise HarnessError("--cookbook: %r comes before the launch command and sets no variable" % w)
        env.append(w)
    groups = []
    for w in words[start + skip:]:
        if w.startswith("--"):
            groups.append([w])
        elif groups:
            groups[-1].append(w)
        else:
            raise HarnessError("--cookbook: %r comes before any flag" % w)
    values, args, model = {}, [], None
    for group in groups:
        flag, eq, inline = group[0].partition("=")
        given = ([inline] if eq else []) + group[1:]
        if flag in COOKBOOK_FLAGS and len(given) == 1:
            values[COOKBOOK_FLAGS[flag]] = (given[0], source)
        elif flag == "--disable-radix-cache" and not given:
            values["PREFIX_CACHE"] = ("0", source)
        elif flag in ("--model", "--model-path"):
            model = given[0] if given else None
        elif flag not in ("--host", "--port"):
            args.append(group)
    return values, {"source": source, "model": model, "env": env, "args": args}


def accuracy_server_env(params):
    """A context the feature caps for its own tests may not hold an accuracy run's answers."""
    if "CONTEXT_LENGTH" not in params or "MAX_TOKENS" not in params:
        return {}
    ctx, max_tokens = params["CONTEXT_LENGTH"]["value"], params["MAX_TOKENS"]["value"]
    if ctx and int(ctx) < int(max_tokens) + QUESTION_ROOM:
        return {"CONTEXT_LENGTH": ""}
    return {}


def cookbook_lines(recipe):
    """server.sh's cookbook block for a recipe: its environment exported, then its other flags, each word quoted."""
    if not recipe:
        return ["cookbook=()"]
    out = ["# the recipe's model: %s (harness.json names its page)" % (recipe["model"] or "none named")]
    for setting in recipe["env"]:
        name, _, value = setting.partition("=")
        out.append("export %s=%s" % (name, shlex.quote(value)))
    out.append("cookbook=(")
    out += ["  " + " ".join(shlex.quote(w) for w in group) for group in recipe["args"]]
    out.append(")")
    return out


def rendered(script, params, recipe=None, record="harness.json"):
    text = (TEMPLATES / ("%s.sh" % script)).read_text(encoding="utf-8")
    lines = text.splitlines()
    for p in parameters(script):
        e = params[p["name"]]
        lines[p["line"]] = '%s="${%s%s%s}"%s# %s; %s [%s]' % (p["name"], p["name"], p["op"], e["value"],
                                                            p["pad"] or " ", p["type"], p["about"], e["source"])
    if script == "server":
        first, last = block(lines, COOKBOOK_BEGIN, COOKBOOK_END, script)
        lines[first + 1:last] = cookbook_lines(recipe)
    lines.insert(1, "# Rendered from templates/harness/%s.sh by scripts/harness.py; %s beside it says when, and "
                    "where each value came from." % (script, record))
    out = "\n".join(lines) + "\n"
    if not re.fullmatch(VALUES["str"], str(ROOT)):
        raise HarnessError("the checkout's path %s cannot be written into a script" % ROOT)
    return INFERPILOT_DIR_LINE.sub(lambda m: 'INFERPILOT_DIR="${INFERPILOT_DIR:-%s}"' % ROOT, out)


def render(out_dir, params, scripts, recipe=None, name=None):
    """Writes scripts to out_dir with harness.json, or the one script as NAME.sh with NAME.json."""
    if name is not None and (len(scripts) != 1 or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", name)
                             or name in ("harness",) + SCRIPTS):
        raise HarnessError("--name takes one script and a plain file name, none of harness, %s" % ", ".join(SCRIPTS))
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    when = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    record_name = "%s.json" % (name or "harness")
    paths = {}
    for script in scripts:
        path = out_dir / ("%s.sh" % (name or script))
        path.write_text(rendered(script, params, recipe, record_name), encoding="utf-8")
        path.chmod(0o755)
        paths[script] = str(path)
    record = {"rendered": when, "templates": str(TEMPLATES), "scripts": paths, "parameters": params,
              "cookbook": recipe if "server" in scripts else None, "accuracy_server_env": accuracy_server_env(params)}
    (out_dir / record_name).write_text(json.dumps(record, indent=1) + "\n", encoding="utf-8")
    return record


def show():
    rows = ["| script | parameter | default | type | meaning |", "|---|---|---|---|---|"]
    for name, p in catalog().items():
        default = p["default"] if p["default"] or p["op"] == ":-" else "(empty)"
        rows.append("| %s.sh | %s | %s | %s | %s |" % (p["script"], name, default or "(required)", p["type"], p["about"]))
    rows.append("")
    rows.append("server.sh also runs the environment and the other flags of the model's sglang cookbook recipe "
                "(render --cookbook).")
    return "\n".join(rows)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("show", help="every parameter, its script, default and meaning")
    r = sub.add_parser("render", help="the scripts with the task's, the feature's, the cookbook's and the templates' values")
    r.add_argument("--out", required=True, help="the directory to write the scripts and harness.json to")
    r.add_argument("--set", action="append", default=[], metavar="NAME=VALUE", help="a task's value")
    r.add_argument("--task", help="the task's values, a JSON object or file (its harness field)")
    r.add_argument("--workload", help="the task's workload, a JSON object with isl, osl and conc")
    r.add_argument("--feature", help="the feature's values, a JSON object or file")
    r.add_argument("--feature-source", default="the feature's source", help="where plain --feature values come from")
    r.add_argument("--cookbook", help="the model's sglang cookbook recipe for this GPU: its launch command, or a file of it")
    r.add_argument("--cookbook-source", default="the sglang cookbook", help="where the recipe comes from: page and selection")
    r.add_argument("--scripts", default=",".join(SCRIPTS), help="which to render (default: %(default)s)")
    r.add_argument("--name", help="write the one script picked as NAME.sh, its record as NAME.json")
    args = ap.parse_args(argv)
    try:
        if args.cmd == "show":
            print(show())
            return 0
        scripts = [s.strip() for s in args.scripts.split(",") if s.strip()]
        unknown = sorted(set(scripts) - set(SCRIPTS))
        if unknown or not scripts:
            raise HarnessError("--scripts takes some of %s" % ", ".join(SCRIPTS))
        cook, recipe = cookbook_values(text_or_file(args.cookbook), args.cookbook_source)
        params = resolve(task_values(args.set, load(args.task, "--task"), load(args.workload, "--workload")),
                         feature_values(load(args.feature, "--feature"), args.feature_source), cook, scripts)
        record = render(args.out, params, scripts, recipe, args.name)
    except HarnessError as e:
        print("harness: %s" % e, file=sys.stderr)
        return 2
    for name, e in record["parameters"].items():
        print("%-22s %-28s %s%s" % (name, e["value"] or "(empty)", e["source"], ": " + e["why"] if e.get("why") else ""))
    if record["cookbook"]:
        print("cookbook: %s; environment %s; other flags %s" % (record["cookbook"]["source"],
              " ".join(record["cookbook"]["env"]) or "none",
              " ".join(" ".join(g) for g in record["cookbook"]["args"]) or "none"))
    print("wrote %s" % ", ".join(record["scripts"].values()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
