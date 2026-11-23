"""Unit tests for inferpilot/bootstrap.py and scripts/restore_owner.sh: nothing is installed, cloned or
linked outside temporary directories (commands are mocked).

    python3 -m unittest discover -s tests
"""
import hashlib
import io
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import unittest
from contextlib import ExitStack, redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from inferpilot import bootstrap as bs  # noqa: E402

OK = SimpleNamespace(returncode=0)


def S(pwd, **env):
    return bs.settings({"PWD": str(pwd), "HOME": str(env.pop("HOME", pwd)), **env})


def make_checkout(root: Path) -> Path:
    (root / ".git").mkdir(parents=True)
    (root / "roles").mkdir()
    (root / "inferpilot.js").write_text("// inferpilot")
    return root


def make_exe(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


class Quiet:
    """Silence stdout and stderr; keep what was written to stderr."""

    def __enter__(self):
        self.err = io.StringIO()
        self._stack = ExitStack()
        self._stack.enter_context(redirect_stdout(io.StringIO()))
        self._stack.enter_context(redirect_stderr(self.err))
        return self

    def __exit__(self, *exc):
        return self._stack.__exit__(*exc)


class VersionAndSettingsTest(unittest.TestCase):
    def test_ver_ge(self):
        self.assertTrue(bs.ver_ge("2.1.206", "2.1.177"))
        self.assertTrue(bs.ver_ge("2.1.177", "2.1.177"))
        self.assertFalse(bs.ver_ge("2.1.99", "2.1.177"))
        self.assertTrue(bs.ver_ge("3.0.0-beta", "2.9.9"))

    def test_claude_version(self):
        self.assertEqual(bs.claude_version(""), "")
        with mock.patch.object(bs, "_out", return_value=(0, "2.1.206 (Claude Code)", "")):
            self.assertEqual(bs.claude_version("/x/claude"), "2.1.206")
        with mock.patch.object(bs, "_out", return_value=(127, "", "not found")):
            self.assertEqual(bs.claude_version("/x/claude"), "")

    def test_settings(self):
        with tempfile.TemporaryDirectory() as d:
            co = make_checkout(Path(d) / "inferpilot")
            self.assertEqual(S(co, HOME=d)["home"], str(co))
            work = Path(d) / "work"
            work.mkdir()
            self.assertEqual(S(work, HOME=d)["home"], str(work / "inferpilot"))
            s = bs.settings({"PWD": str(work), "HOME": d, "INFERPILOT_FRAMEWORK_DIR": "~/sglang"}, {"framework_repo": "/r"})
            self.assertEqual((s["framework_dir"], s["framework_repo"]), (os.path.join(d, "sglang"), "/r"))
            self.assertEqual(s["claude_bin_dir"], os.path.join(d, ".local", "bin"))
            self.assertEqual((s["evalscope_venv"], s["evalscope_version"]), (bs.EVALSCOPE_VENV, ""))
            s = bs.settings({"PWD": str(work), "HOME": d, "EVALSCOPE_VENV": "~/es", "EVALSCOPE_VERSION": "1.15.0"})
            self.assertEqual((s["evalscope_venv"], s["evalscope_version"]), (os.path.join(d, "es"), "1.15.0"))
            self.assertFalse(s["cursor"])
            s = bs.settings({"PWD": str(work), "HOME": d, "INFERPILOT_CURSOR": "1", "INFERPILOT_NODE_VERSION": "22"})
            self.assertEqual((s["cursor"], s["node_version"]), (True, "22"))
            self.assertTrue(bs.settings({"PWD": str(work), "HOME": d}, {"cursor": True})["cursor"])
            pick = lambda opts, env=None: (lambda s: (s["claude"], s["cursor"], s["runner"]))(
                bs.settings(dict({"PWD": str(work), "HOME": d}, **(env or {})), opts))
            self.assertEqual(pick({}), (True, False, False), "neither: Claude Code")
            self.assertEqual(pick({"cursor": True}), (False, True, True), "--cursor: the Cursor CLI and Node.js")
            self.assertEqual(pick({"cursor": True, "claude": True}), (True, True, True))
            self.assertEqual(pick({"runner": True}), (True, False, True))
            self.assertEqual(pick({}, {"INFERPILOT_CURSOR": "1", "INFERPILOT_CLAUDE": "1"}), (True, True, True))


class KeysTest(unittest.TestCase):
    """The keys file: the one run_docker.sh mounts as root's ~/.inferpilot_env, else, in a container that
    mounts /home, the one in your home there."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.root = self.tmp / "root"
        self.root.mkdir()

    @unittest.skipIf(os.geteuid() == 0, "as root, what the test makes belongs to root, not to a user as on a host mount")
    def test_the_mounted_keys_file_comes_first(self):
        co = make_checkout(self.tmp / "u" / "inferpilot")
        (self.tmp / "u" / ".inferpilot_env").write_text("export LLM_GATEWAY_KEY=k\n")
        s = S("/", HOME=str(self.root), INFERPILOT_HOME=str(co))
        self.assertEqual(bs.keys_file(s), str(self.tmp / "u" / ".inferpilot_env"), "the checkout's owner's home")
        (self.root / ".inferpilot_env").write_text("export LLM_GATEWAY_KEY=k\n")
        self.assertEqual(bs.keys_file(s), str(self.root / ".inferpilot_env"))
        with Quiet():
            self.assertEqual(bs.ensure_keys(s), ("kept", str(self.root / ".inferpilot_env")))
        given = S("/", HOME=str(self.root), INFERPILOT_HOME=str(co), INFERPILOT_KEYS=str(self.tmp / "mine"))
        self.assertEqual(bs.keys_file(given), "", "a INFERPILOT_KEYS that is not a file wins over the others")
        self.assertIn("INFERPILOT_KEYS=%s is not a file" % (self.tmp / "mine"), bs.keys_missing(given))
        (self.tmp / "mine").write_text("")
        self.assertEqual(bs.keys_file(given), str(self.tmp / "mine"))

    def test_inferpilot_host_home_names_your_home(self):
        home = self.tmp / "u"
        home.mkdir()
        co = make_checkout(self.tmp / "workspace" / "inferpilot")
        s = S("/", HOME=str(self.root), INFERPILOT_HOME=str(co), INFERPILOT_HOST_HOME=str(home))
        self.assertEqual((bs.owner_dirs(s), bs.keys_file(s)), ([str(home)], ""))
        self.assertIn("nor one in %s" % home, bs.keys_missing(s))
        (home / ".inferpilot_env").write_text("")
        self.assertEqual(bs.keys_file(s), str(home / ".inferpilot_env"))
        gone = S("/", HOME=str(self.root), INFERPILOT_HOME=str(co), INFERPILOT_HOST_HOME=str(self.tmp / "elsewhere"))
        self.assertEqual((bs.owner_dirs(gone), bs.keys_file(gone)), ([], ""))
        self.assertIn("(not mounted)", bs.keys_missing(gone))

    def test_no_keys_file_sets_up_the_rest(self):
        s = S("/", HOME=str(self.root), INFERPILOT_HOME="/nonexistent/inferpilot")
        with mock.patch.object(bs, "container_name", return_value=""), Quiet() as q:
            self.assertEqual(bs.ensure_keys(s), ("skipped", "no keys file"))
        self.assertIn("no %s" % (self.root / ".inferpilot_env"), q.err.getvalue())

    def test_a_checkout_of_root_names_no_home(self):
        """InferPilot cloned into /workspace belongs to root, which says nothing about your home."""
        s = S("/", HOME="/root", INFERPILOT_HOME=str(make_checkout(self.tmp / "workspace" / "inferpilot")))
        of_root = os.stat_result((0o40755, 0, 0, 1, 0, 0, 0, 0, 0, 0))
        with mock.patch.object(bs.os, "stat", return_value=of_root):
            self.assertEqual(bs.owner_dirs(s), [])

    def test_a_home_from_the_container_name(self):
        """As run_docker.sh names containers: your login, or your login and a suffix."""
        homes = self.tmp / "home"
        (homes / "alice").mkdir(parents=True)
        (homes / "alice" / ".inferpilot_env").write_text("")
        (homes / "ali").mkdir()
        (homes / "ali" / ".inferpilot_env").write_text("")
        make_exe(homes / "bare" / ".local" / "bin" / "claude")
        for name in ("alice", "alice-0116", "alice_sgl", "alice.dev", "inferpilot_alice_1118_k3x9"):
            self.assertEqual(bs.home_from_container_name(name, str(homes)), str(homes / "alice"), name)
        self.assertEqual(bs.home_from_container_name("ali-test", str(homes)), str(homes / "ali"))
        self.assertEqual(bs.home_from_container_name("inferpilot_ali_1118_k3x9", str(homes)), str(homes / "ali"))
        (homes / "inferpilot").mkdir()
        (homes / "inferpilot" / ".inferpilot_env").write_text("")
        self.assertEqual(bs.home_from_container_name("inferpilot_alice_1118_k3x9", str(homes)), str(homes / "alice"),
                         "the user after the prefix")
        self.assertEqual(bs.home_from_container_name("inferpilot_1118_k3x9", str(homes)), str(homes / "inferpilot"),
                         "a user named inferpilot")
        for name in ("alicex", "bare", "bare-1", "nobody", "a"):
            self.assertEqual(bs.home_from_container_name(name, str(homes)), "", name)
        self.assertEqual(bs.home_from_container_name("alice", str(homes / "missing")), "")

    def test_container_name_from_the_variable_or_docker(self):
        self.assertEqual(bs.container_name({"INFERPILOT_CONTAINER": "box"}), "box")
        cid = "a" * 64
        info = "1 0 8:1 /var/lib/docker/containers/%s/resolv.conf /etc/resolv.conf rw - ext4 /dev/sda1 rw\n" % cid
        with mock.patch.object(bs, "_read", return_value=info), mock.patch.object(bs, "_has", return_value=True), \
                mock.patch.object(bs, "_out", return_value=(0, "/alice", "")) as out:
            self.assertEqual(bs.container_name({}), "alice")
        self.assertEqual(out.call_args[0][0][-1], cid)
        with mock.patch.object(bs, "_read", return_value=info), mock.patch.object(bs, "_has", return_value=False):
            self.assertEqual(bs.container_name({}), "", "no Docker CLI")
        with mock.patch.object(bs, "_read", return_value=info), mock.patch.object(bs, "_has", return_value=True), \
                mock.patch.object(bs, "_out", return_value=(1, "", "Cannot connect to the Docker daemon")):
            self.assertEqual(bs.container_name({}), "", "no socket")
        with mock.patch.object(bs, "_read", return_value=""):
            self.assertEqual(bs.container_name({}), "", "not in a container")

    def test_a_container_named_after_you_finds_your_keys(self):
        """A container your own docker run started with /home mounted, named after your login."""
        homes = self.tmp / "home"
        (homes / "alice").mkdir(parents=True)
        (homes / "alice" / ".inferpilot_env").write_text("")
        s = S("/", HOME=str(self.root), INFERPILOT_HOME="/nonexistent/inferpilot")
        with mock.patch.object(bs, "HOMES", str(homes)), \
                mock.patch.object(bs, "container_name", return_value="alice-0116"), Quiet() as q:
            self.assertEqual(bs.ensure_keys(s), ("found", str(homes / "alice" / ".inferpilot_env")))
        self.assertIn("named after (alice-0116)", q.err.getvalue())
        self.assertIn("INFERPILOT_KEYS=<your keys file>", q.err.getvalue())
        self.assertEqual(bs.keys_file(s), str(homes / "alice" / ".inferpilot_env"))
        s = S("/", HOME=str(self.root), INFERPILOT_HOME="/nonexistent/inferpilot")
        with mock.patch.object(bs, "HOMES", str(homes)), \
                mock.patch.object(bs, "container_name", return_value="box"), Quiet() as q:
            self.assertEqual(bs.ensure_keys(s)[0], "skipped")
        self.assertIn("matches this container's name (box)", q.err.getvalue())
        for given in (S("/", HOME=str(self.root), INFERPILOT_HOME="/nonexistent/inferpilot", INFERPILOT_HOST_HOME=str(self.tmp / "x")),
                      S("/", HOME=str(self.root), INFERPILOT_HOME="/nonexistent/inferpilot", INFERPILOT_KEYS=str(self.tmp / "x"))):
            with mock.patch.object(bs, "HOMES", str(homes)), \
                    mock.patch.object(bs, "container_name", return_value="alice") as name, Quiet():
                self.assertEqual(bs.ensure_keys(given)[0], "skipped", "a INFERPILOT_HOST_HOME or INFERPILOT_KEYS you set wins")
            name.assert_not_called()


class CheckoutTest(unittest.TestCase):
    def test_existing_checkouts_are_kept(self):
        with tempfile.TemporaryDirectory() as d:
            co = make_checkout(Path(d) / "inferpilot")
            with mock.patch.object(bs, "_run") as run, Quiet():
                self.assertEqual(bs.ensure_checkout(S(co))[0], "kept")
                self.assertEqual(bs.ensure_checkout(S(d, INFERPILOT_HOME=str(co)))[0], "kept")
            run.assert_not_called()

    def test_other_content_is_left_alone(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "inferpilot").mkdir()
            (Path(d) / "inferpilot" / "notes.txt").write_text("mine")
            with mock.patch.object(bs, "_run") as run, Quiet():
                self.assertEqual(bs.ensure_checkout(S(d))[0], "skipped")
            run.assert_not_called()
            self.assertEqual((Path(d) / "inferpilot" / "notes.txt").read_text(), "mine")

    def test_clone_only_when_missing(self):
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.object(bs, "_has", return_value=True), mock.patch.object(bs, "give_back") as back, \
                    mock.patch.object(bs, "_run", return_value=OK) as run, Quiet():
                self.assertEqual(bs.ensure_checkout(S(d, INFERPILOT_REF="main"))[0], "created")
            run.assert_called_once_with(["git", "clone", "--branch", "main", bs.DEFAULT_REPO_URL, str(Path(d) / "inferpilot")])
            back.assert_called_once_with(str(Path(d) / "inferpilot"))
            with mock.patch.object(bs, "_has", return_value=False), Quiet():
                self.assertEqual(bs.ensure_checkout(S(d))[0], "failed")


class GiveBackTest(unittest.TestCase):
    @unittest.skipIf(os.geteuid() == 0, "as root, what the test makes belongs to root, not to a user as on a host mount")
    def test_root_gives_back_to_the_closest_owner(self):
        with tempfile.TemporaryDirectory() as d:
            new = Path(d) / "a" / "b"
            new.mkdir(parents=True)
            st = os.stat(d)
            with mock.patch.object(bs, "is_root", return_value=True), \
                    mock.patch.object(bs, "_run", return_value=OK) as run, Quiet():
                bs.give_back(str(new))
            run.assert_called_once_with(["chown", "-R", "%d:%d" % (st.st_uid, st.st_gid), str(new)])
            with mock.patch.object(bs, "is_root", return_value=False), mock.patch.object(bs, "_run") as run:
                bs.give_back(str(new))
            run.assert_not_called()

    @unittest.skipIf(os.geteuid() == 0, "checks the non-root path")
    def test_restore_owner_script_does_nothing_without_root(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "f"
            f.write_text("x")
            before = os.stat(f)
            r = subprocess.run(["sh", str(ROOT / "scripts" / "restore_owner.sh"), str(f), str(Path(d) / "missing")])
            self.assertEqual(r.returncode, 0)
            self.assertEqual((os.stat(f).st_uid, os.stat(f).st_gid), (before.st_uid, before.st_gid))


@unittest.skipUnless(shutil.which("bash"), "needs bash")
class RunDockerTest(unittest.TestCase):
    """scripts/run_docker.sh against a fake docker that logs its arguments: a container is never removed."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.log = self.tmp / "docker.log"
        fake = self.tmp / "bin"
        make_exe(fake / "docker").write_text(
            '#!/bin/sh\necho "$*" >> "$DOCKER_LOG"\n'
            'if [ "$1" = inspect ] && [ -n "$FAKE_TAKEN_ONCE" ] && [ ! -e "$DOCKER_LOG.taken" ]; then '
            ': > "$DOCKER_LOG.taken"; exit 0; fi\n'
            'if [ "$1" = inspect ]; then [ -n "$FAKE_IMAGE" ] || exit 1; echo "$FAKE_IMAGE"; fi\n'
            'if [ "$1" = ps ] && [ -n "$FAKE_PS" ]; then printf "%s\\n" "$FAKE_PS"; fi\n'
            'if [ "$1 $2" = "image inspect" ] && [ -n "$FAKE_MISSING" ]; then exit 1; fi\n'
            'if [ "$1" = run ] && [ -n "$FAKE_DENY" ]; then echo "$FAKE_DENY" >&2; exit 125; fi\n'
            'if [ "$1 $2" = "exec -w" ]; then [ -z "$FAKE_PULL_FAIL" ] || { echo "$FAKE_PULL_FAIL" >&2; exit 1; }; '
            'echo "b2cc023f23 2026-11-17"; fi\n'
            'if [ "$1" = images ]; then printf "%s\\n" $FAKE_LOCAL; fi\n')
        make_exe(fake / "sudo").write_text('#!/bin/sh\nexec "$@"\n')
        make_exe(fake / "rocm-smi").write_text(
            '#!/bin/sh\n[ -n "$FAKE_GFX" ] && echo "GPU[0] : GFX Version: $FAKE_GFX"\n')
        make_exe(fake / "rocminfo").write_text("#!/bin/sh\n")
        self.path = "%s:%s" % (fake, os.environ.get("PATH", "/usr/bin:/bin"))

    def run_docker(self, existing="", image="", **extra):
        # stdin is never a terminal here; DETACH=0 stands for the interactive case. NAME="" for a generated name.
        env = {"PATH": self.path, "HOME": str(self.tmp / "home"), "USER": "tester", "DOCKER_LOG": str(self.log),
               "FAKE_IMAGE": existing, "IMAGE": image, "DETACH": "0", "NAME": "tester", **extra}
        r = subprocess.run(["bash", str(ROOT / "scripts" / "run_docker.sh")], env=env, capture_output=True, text=True,
                           stdin=subprocess.DEVNULL)
        self.stderr = r.stderr
        calls = self.log.read_text().splitlines() if self.log.exists() else []
        self.log.unlink(missing_ok=True)
        return r.returncode, calls

    @staticmethod
    def created(calls):
        """The `docker run` that created the container."""
        return next(c for c in calls if c.startswith("run "))

    def daily(self):
        """PATH with a python3 that answers latest_image.py: the newest daily image is ...-20261117."""
        fake_py = self.tmp / "py"
        make_exe(fake_py / "python3").write_text('#!/bin/sh\necho "rocm/sgl-dev:v0.5.23-rocm10-mi35x-20261117"\n')
        return "%s:%s" % (fake_py, self.path)

    def test_parses(self):
        self.assertEqual(subprocess.run(["bash", "-n", str(ROOT / "scripts" / "run_docker.sh")]).returncode, 0)

    def test_an_existing_container_is_entered_again_its_code_as_it_was_left(self):
        """With your home, also in a container this script did not start."""
        inspect = "inspect --type container -f {{.Config.Image}} tester"
        enter = [inspect, "start tester", "exec -it -w /workspace -e INFERPILOT_CONTAINER=tester tester bash"]
        self.assertEqual(self.run_docker(existing="rocm/sgl-dev:old", PATH=self.daily()), (0, enter))
        taken = "WARNING: the name tester is taken: you enter the old container of that name (name tester, image "
        self.assertIn(taken + "rocm/sgl-dev:old), not a new one, so its /sgl-workspace/sglang is as it was left",
                      self.stderr)
        self.assertEqual(self.run_docker(existing="rocm/sgl-dev:old", image="rocm/sgl-dev:old", PATH=self.daily()),
                         (0, enter))
        self.assertIn(taken + "rocm/sgl-dev:old), not a new one; its code is as it was left", self.stderr)
        self.assertNotIn("another image version", self.stderr)
        self.assertEqual(self.run_docker(existing="rocm/sgl-dev:old", image="rocm/sgl-dev:new", PATH=self.daily()),
                         (0, enter))
        self.assertIn(taken + "rocm/sgl-dev:old, not rocm/sgl-dev:new: another image version than the one you asked "
                      "for), not a new one", self.stderr)
        self.assertEqual(self.stderr.count("WARNING"), 1, "one warning says both")
        self.assertEqual(self.run_docker(existing="rocm/sgl-dev:old", image="docker.io/rocm/sgl-dev:old", PATH=self.daily()),
                         (0, enter))
        self.assertNotIn("another image version", self.stderr, "the same image, spelt another way")

    def test_the_hints_say_how_to_run_it_again(self):
        """Read from curl, the script has no path of its own; without USER, the name is still yours."""
        with open(ROOT / "scripts" / "run_docker.sh") as script:
            r = subprocess.run(["bash"], stdin=script, capture_output=True, text=True,
                               env={"PATH": self.path, "HOME": str(self.tmp / "home"), "USER": "tester",
                                    "DOCKER_LOG": str(self.log), "IMAGE": "rocm/sgl-dev:new", "DETACH": "1"})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("come back to it with: NAME=inferpilot_tester_", r.stderr)
        self.assertIn(" bash <(curl -fsSL https://raw.githubusercontent.com/At1a8/inferpilot/main/scripts/run_docker.sh)",
                      r.stderr)
        self.log.unlink()
        rc, calls = self.run_docker(image="rocm/sgl-dev:new", NAME="", USER="")
        self.assertEqual(rc, 0, self.stderr)
        # As run_docker.sh has it: what id -un prints (a uid without a passwd entry prints its number, or nothing),
        # its odd characters as _.
        who = subprocess.run(["id", "-un"], capture_output=True, text=True)
        me = re.sub(r"[^a-zA-Z0-9_.-]", "_", who.stdout.strip())
        me = re.escape(me if re.match(r"[a-zA-Z0-9]", me) else "u" + me)
        self.assertRegex(self.created(calls), r"--label inferpilot\.user=%s --entrypoint /bin/bash --name "
                                              r"inferpilot_%s_\d{4}_[a-z0-9]{4} rocm/sgl-dev:new$" % (me, me))

    def test_a_new_container_from_the_daily_image_starts_on_the_newest_main(self):
        main = ("exec -w /sgl-workspace/sglang tester bash -c git checkout -- . && git checkout -q main && git pull -q"
                ' && git log -1 --format="%h %cs"')
        rc, calls = self.run_docker(PATH=self.daily())
        self.assertEqual((rc, [c.split()[0] for c in calls[-4:]]), (0, ["run", "exec", "ps", "exec"]), self.stderr)
        self.assertIn("-e INFERPILOT_IMAGE_PINNED=0", calls[-4])
        self.assertEqual((calls[-3], calls[-1]), (main, "exec -it -w /workspace -e INFERPILOT_CONTAINER=tester tester bash"))
        self.assertIn("/sgl-workspace/sglang is on main at b2cc023f23 2026-11-17", self.stderr)
        rc, calls = self.run_docker(image="rocm/sgl-dev:new")
        self.assertEqual((rc, [c.split()[0] for c in calls]), (0, ["inspect", "image", "run", "ps", "exec"]),
                         "a given image's code stays")
        rc, calls = self.run_docker(PATH=self.daily(), DETACH="1")
        self.assertEqual((rc, calls[-2]), (0, main), "without a terminal too")

    def test_without_name_a_new_container_gets_a_name_no_container_has(self):
        today = lambda: subprocess.run(["date", "+%m%d"], env={"TZ": "Asia/Shanghai"}, capture_output=True,
                                       text=True).stdout.strip()
        days = {today()}
        rc, calls = self.run_docker(image="rocm/sgl-dev:new", NAME="", FAKE_TAKEN_ONCE="1")
        days.add(today())
        self.assertEqual(rc, 0, self.stderr)
        tried = [c.split()[-1] for c in calls if c.startswith("inspect --type container ") and " -f " not in c]
        self.assertEqual(len(tried), 2, "the first name is taken, the second is free")
        for name in tried:
            self.assertRegex(name, r"^inferpilot_tester_\d{4}_[a-z0-9]{4}$")
            self.assertIn(name.split("_")[2], days, "the date in UTC+8")
        name = tried[1]
        self.assertTrue(self.created(calls).endswith("--label inferpilot.user=tester --entrypoint /bin/bash --name %s "
                                                     "rocm/sgl-dev:new" % name), self.created(calls))
        self.assertIn("-e INFERPILOT_CONTAINER=%s" % name, self.created(calls))
        self.assertEqual(calls[-1], "exec -it -w /workspace -e INFERPILOT_CONTAINER=%s %s bash" % (name, name))
        self.assertIn("this is container %s (image rocm/sgl-dev:new); come back to it with: NAME=%s " % (name, name),
                      self.stderr)

    def test_a_new_container_lists_your_other_ones(self):
        others = "inferpilot_tester_1101_ab12|rocm/sgl-dev:v0.5.23-rocm10-mi35x-20261101|2 weeks ago|Exited (0) 3 days ago"
        rc, _ = self.run_docker(image="rocm/sgl-dev:new", FAKE_PS=others + "\ntester|rocm/sgl-dev:new|1 second ago|Up 1 second")
        self.assertEqual(rc, 0, self.stderr)
        self.assertIn("your other InferPilot containers (`", self.stderr)
        self.assertIn("docker rm -f <name>` removes one you are done with", self.stderr)
        self.assertIn("  inferpilot_tester_1101_ab12  rocm/sgl-dev:v0.5.23-rocm10-mi35x-20261101  (created 2 weeks ago; Exited (0) "
                      "3 days ago)", self.stderr)
        self.assertNotIn("  tester  rocm/sgl-dev:new", self.stderr, "not the new one itself")
        rc, _ = self.run_docker(image="rocm/sgl-dev:new")
        self.assertNotIn("your other InferPilot containers", self.stderr)

    def test_a_missing_image_is_pulled_in_sight_first(self):
        rc, calls = self.run_docker(image="rocm/sgl-dev:new", FAKE_MISSING="1")
        self.assertEqual((rc, calls[1:3]), (0, ["image inspect rocm/sgl-dev:new", "pull rocm/sgl-dev:new"]))
        self.assertTrue(calls[3].startswith("run -dit "), calls)
        rc, calls = self.run_docker(image="rocm/sgl-dev:new")
        self.assertNotIn("pull rocm/sgl-dev:new", calls)

    def test_a_failed_pull_is_said_and_the_shell_still_opens(self):
        rc, calls = self.run_docker(PATH=self.daily(), FAKE_PULL_FAIL="fatal: unable to access 'https://github.com/'")
        self.assertEqual((rc, calls[-1]), (0, "exec -it -w /workspace -e INFERPILOT_CONTAINER=tester tester bash"))
        self.assertIn("WARNING: could not put /sgl-workspace/sglang on the newest main: fatal: unable to access", self.stderr)

    def test_entering_a_container_on_an_older_image_says_so(self):
        fake_py = self.tmp / "py"
        make_exe(fake_py / "python3").write_text('#!/bin/sh\necho "older rocm/sgl-dev:v0.5.23-rocm10-mi35x-20261109"\n')
        env = {"PATH": "%s:%s" % (fake_py, self.path), "HOME": str(self.tmp / "home"), "USER": "tester",
               "DOCKER_LOG": str(self.log), "FAKE_IMAGE": "rocm/sgl-dev:v0.5.23-rocm10-mi35x-20261105", "IMAGE": "",
               "DETACH": "0", "NAME": "tester"}
        r = subprocess.run(["bash", str(ROOT / "scripts" / "run_docker.sh")], env=env, capture_output=True, text=True,
                           stdin=subprocess.DEVNULL)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("not the newest daily image any more; that is rocm/sgl-dev:v0.5.23-rocm10-mi35x-20261109", r.stderr)
        self.assertIn("For the newest sglang, start a new container: %s" % (ROOT / "scripts" / "run_docker.sh"), r.stderr)
        self.assertTrue(self.log.read_text().splitlines()[-1].startswith("exec -it -w /workspace "), "it still goes in")

    def test_a_new_container_starts_in_workspace(self):
        rc, calls = self.run_docker(image="rocm/sgl-dev:new")
        self.assertEqual((rc, len(calls), calls[2].split()[:2]), (0, 5, ["run", "-dit"]))
        self.assertEqual(calls[4], "exec -it -w /workspace -e INFERPILOT_CONTAINER=tester tester bash")
        run = calls[2]
        self.assertIn("-w /workspace", run)
        self.assertTrue(run.endswith("--name tester rocm/sgl-dev:new"), run)
        self.assertIn("-e INFERPILOT_IMAGE=rocm/sgl-dev:new -e INFERPILOT_IMAGE_PINNED=1", run, "IMAGE was given")
        self.assertIn("-v /home:/home", run)
        self.assertIn("-e INFERPILOT_HOST_HOME=%s" % (self.tmp / "home"), run)
        self.assertIn("--privileged", run)
        self.assertNotIn(".inferpilot_env", run, "no keys file to mount")

    def test_a_new_container_selects_the_image_variant_for_the_gpu(self):
        fake_py = self.tmp / "py"
        # run_docker.sh calls `python3 <latest_image.py> --variant <variant>`; Docker Hub has FAKE_HUB's variants
        make_exe(fake_py / "python3").write_text(
            '#!/bin/sh\ncase " $FAKE_HUB " in *" $3 "*) echo "rocm/sgl-dev:v0.5.23-$3-20261117" ;; *) exit 1 ;; esac\n')
        hub = "rocm10-mi30x rocm10-mi35x rocm724-mi30x"
        local = "rocm/sgl-dev:v0.5.13.post1-rocm720-mi30x-20260625 rocm/sgl-dev:v0.5.23-rocm724-mi30x-20261116"
        for env, want in (
                (dict(FAKE_GFX="gfx942", FAKE_HUB=hub), "v0.5.23-rocm10-mi30x-20261117"),
                (dict(FAKE_GFX="gfx950", FAKE_HUB=hub), "v0.5.23-rocm10-mi35x-20261117"),
                (dict(FAKE_GFX="gfx942", FAKE_HUB="rocm724-mi30x"), "v0.5.23-rocm724-mi30x-20261117"),
                (dict(FAKE_GFX="gfx942", FAKE_LOCAL=local), "v0.5.23-rocm724-mi30x-20261116"),
                (dict(FAKE_GFX="gfx942", FAKE_HUB=hub, IMAGE_VARIANT="rocm724-mi30x"), "v0.5.23-rocm724-mi30x-20261117"),
                (dict(FAKE_GFX="gfx942", FAKE_LOCAL=local, IMAGE_VARIANT="rocm10-mi30x"), "")):
            with self.subTest(**env):
                rc, calls = self.run_docker(PATH="%s:%s" % (fake_py, self.path), **env)
                if not want:
                    self.assertEqual((rc, calls[-1].split()[0]), (1, "images"), "IMAGE_VARIANT is not widened")
                    continue
                self.assertEqual(rc, 0, self.stderr)
                self.assertTrue(self.created(calls).endswith("rocm/sgl-dev:" + want), calls)
                self.assertIn("-e INFERPILOT_IMAGE_PINNED=0", self.created(calls), "the image was chosen for you")

    def test_without_a_terminal_it_starts_detached_and_says_how_to_enter(self):
        enter = "docker exec -it -w /workspace -e INFERPILOT_CONTAINER=tester tester bash"
        rc, calls = self.run_docker(image="rocm/sgl-dev:new", DETACH="")
        self.assertEqual((rc, self.created(calls).split()[:2]), (0, ["run", "-dit"]), self.stderr)
        self.assertTrue(self.created(calls).endswith("--name tester rocm/sgl-dev:new"), calls)
        self.assertFalse(calls[-1].startswith("exec -it"), "no shell opened")
        self.assertIn(enter, self.stderr)

        rc, calls = self.run_docker(existing="rocm/sgl-dev:new", DETACH="")
        self.assertEqual((rc, calls), (0, ["inspect --type container -f {{.Config.Image}} tester", "start tester"]))
        self.assertIn(enter, self.stderr)

    def test_privileged_0_leaves_it_out_and_keeps_the_gpus(self):
        rc, calls = self.run_docker(image="rocm/sgl-dev:new", PRIVILEGED="0")
        self.assertEqual(rc, 0)
        self.assertNotIn("--privileged", self.created(calls))
        self.assertIn("--device=/dev/kfd --device=/dev/dri --group-add video", self.created(calls))

    def test_a_refused_run_names_the_way_out(self):
        # the two refusals a docker-authz plugin gave on a real cluster, --privileged first
        for deny, hint in (("denied [P1]: --privileged -- request specific adds as necessary for your workload",
                            "run again with PRIVILEGED=0"),
                           ("denied [BH]: /home:/home -- mount your own directory instead, e.g. -v $HOME:$HOME",
                            "run again with MOUNT_HOME=0")):
            msg = "docker: Error response from daemon: authorization denied by plugin docker-authz: docker-authz: " + deny
            rc, _ = self.run_docker(image="rocm/sgl-dev:new", DETACH="1", FAKE_DENY=msg)
            self.assertEqual(rc, 1)
            self.assertIn(deny, self.stderr)
            self.assertIn(hint, self.stderr)

    def test_mount_home_0_leaves_home_out(self):
        """For a task that must not see the homes on the machine, such as a clean-room re-implementation."""
        rc, calls = self.run_docker(image="rocm/sgl-dev:new", MOUNT_HOME="0")
        self.assertEqual(rc, 0)
        self.assertNotIn("-v /home:/home", self.created(calls))
        self.assertNotIn("INFERPILOT_HOST_HOME", self.created(calls))
        self.assertIn("-w /workspace", self.created(calls))

    def test_the_keys_file_is_mounted_read_only(self):
        home = self.tmp / "home"
        home.mkdir()
        (home / ".inferpilot_env").write_text("export LLM_GATEWAY_KEY=k\n")
        rc, calls = self.run_docker(image="rocm/sgl-dev:new")
        self.assertIn("-v %s:/root/.inferpilot_env:ro" % (home / ".inferpilot_env"), self.created(calls))


class ClaudeCodeTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.s = S(tmp.name)

    def test_install_off_path_is_found_and_kept(self):
        exe = make_exe(Path(self.s["claude_bin_dir"]) / "claude")
        with mock.patch.object(bs.shutil, "which", return_value=None):
            self.assertEqual(bs.find_claude(self.s), str(exe))
            with mock.patch.object(bs, "claude_version", return_value="2.1.317"), \
                    mock.patch.object(bs, "install_claude") as inst, Quiet():
                self.assertEqual(bs.ensure_claude_code(self.s), ("kept", "2.1.317"))
        inst.assert_not_called()

    def test_old_version_is_updated(self):
        with mock.patch.object(bs, "find_claude", return_value="/x/claude"), \
                mock.patch.object(bs, "claude_version", side_effect=["2.1.100", "2.1.200"]), \
                mock.patch.object(bs, "install_claude") as inst, mock.patch.object(bs, "_run") as run, Quiet():
            self.assertEqual(bs.ensure_claude_code(self.s), ("updated", "2.1.200"))
        run.assert_called_once_with(["/x/claude", "update"])
        inst.assert_not_called()

    def test_update_not_enough_then_install(self):
        with mock.patch.object(bs, "find_claude", return_value="/x/claude"), \
                mock.patch.object(bs, "claude_version", side_effect=["2.1.100", "2.1.100", "2.1.206"]), \
                mock.patch.object(bs, "install_claude", return_value=True) as inst, mock.patch.object(bs, "_run"), Quiet():
            self.assertEqual(bs.ensure_claude_code(self.s), ("installed", "2.1.206"))
        inst.assert_called_once()

    def test_missing_is_installed_or_reported(self):
        with mock.patch.object(bs, "find_claude", side_effect=["", "/x/claude"]), \
                mock.patch.object(bs, "claude_version", side_effect=["", "2.1.206"]), \
                mock.patch.object(bs, "install_claude", return_value=True) as inst, Quiet():
            self.assertEqual(bs.ensure_claude_code(self.s), ("installed", "2.1.206"))
        inst.assert_called_once_with(self.s)
        with mock.patch.object(bs, "find_claude", return_value=""), \
                mock.patch.object(bs, "install_claude", return_value=False), Quiet():
            self.assertEqual(bs.ensure_claude_code(self.s)[0], "failed")

    def test_installers(self):
        with mock.patch.object(bs, "_has", side_effect=lambda c: c == "curl"), \
                mock.patch.object(bs, "_run", return_value=OK) as run, Quiet():
            self.assertTrue(bs.install_claude(self.s))
        self.assertIn("https://claude.ai/install.sh | bash -s latest", run.call_args[0][0])
        self.assertTrue(run.call_args[1].get("shell"))
        with mock.patch.object(bs, "_has", return_value=True), \
                mock.patch.object(bs, "_run", side_effect=[SimpleNamespace(returncode=1), OK]) as run, Quiet():
            self.assertTrue(bs.install_claude(self.s))
        self.assertEqual(run.call_args[0][0], ["npm", "install", "-g", "@anthropic-ai/claude-code"])
        with mock.patch.object(bs, "_has", return_value=False), Quiet():
            self.assertFalse(bs.install_claude(self.s))


class ShellRcTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name)
        self.s = S(self.home, SHELL="/bin/bash")
        self.rc = self.home / ".bashrc"

    def test_path_skipped_without_a_native_install(self):
        with Quiet():
            self.assertEqual(bs.ensure_path(self.s, path_env="/usr/bin")[0], "skipped")
        self.assertFalse(self.rc.exists())

    def test_path_added_once_and_kept_after(self):
        make_exe(Path(self.s["claude_bin_dir"]) / "claude")
        self.rc.write_text("alias ll='ls -l'\n")
        with Quiet():
            self.assertEqual(bs.ensure_path(self.s, path_env="/usr/bin")[0], "added")
            self.assertEqual(bs.ensure_path(self.s, path_env="/usr/bin")[0], "kept")
            self.assertEqual(bs.ensure_path(self.s, path_env=self.s["claude_bin_dir"])[0], "kept")
        text = self.rc.read_text()
        self.assertTrue(text.startswith("alias ll='ls -l'\n"))
        self.assertEqual(text.count(bs.RC_BEGIN), 1)

    @unittest.skipUnless(shutil.which("bash"), "needs bash")
    def test_path_block_never_duplicates_an_entry(self):
        make_exe(Path(self.s["claude_bin_dir"]) / "claude")
        with Quiet():
            bs.ensure_path(self.s, path_env="/usr/bin")
        out = subprocess.run(["bash", "-c", 'source "$1"; source "$1"; echo "$PATH"', "_", str(self.rc)],
                             env={"PATH": "/usr/bin:/bin", "HOME": str(self.home)}, capture_output=True, text=True).stdout
        self.assertEqual(out.strip().split(":").count(self.s["claude_bin_dir"]), 1)

    def test_existing_path_entry_is_respected(self):
        make_exe(Path(self.s["claude_bin_dir"]) / "claude")
        self.rc.write_text('export PATH="$HOME/.local/bin:$PATH"\n')
        with Quiet():
            self.assertEqual(bs.ensure_path(self.s, path_env="/usr/bin")[0], "kept")
        self.assertEqual(self.rc.read_text(), 'export PATH="$HOME/.local/bin:$PATH"\n')
        self.assertEqual(bs.rc_file(S(self.home, SHELL="/usr/bin/zsh")), str(self.home / ".zshrc"))

    @unittest.skipUnless(shutil.which("bash"), "needs bash")
    @unittest.skipIf(os.geteuid() == 0, "as root, what the test makes belongs to root, not to a user as on a host mount")
    def test_container_env_and_keys_added_once(self):
        host = self.home / "u"
        co = make_checkout(host / "inferpilot")
        make_exe(host / ".local" / "bin" / "claude")
        (host / ".inferpilot_env").write_text("export ANTHROPIC_API_KEY=sk-test\n")
        s = S("/", HOME=str(self.home), SHELL="/bin/bash", INFERPILOT_HOME=str(co))
        with Quiet():
            self.assertEqual(bs.ensure_shell_env(s)[0], "added")
            self.assertEqual(bs.ensure_shell_env(s)[0], "kept")
        self.assertEqual(self.rc.read_text().count(bs.ENV_BEGIN), 1)
        out = subprocess.run(["bash", "-c", 'source "$1"; echo "$PYTHONDONTWRITEBYTECODE $DISABLE_AUTOUPDATER $ANTHROPIC_API_KEY"',
                              "_", str(self.rc)], env={"PATH": "/usr/bin:/bin"}, capture_output=True, text=True).stdout
        self.assertEqual(out.strip(), "1 1 sk-test")

    @unittest.skipUnless(shutil.which("bash"), "needs bash")
    def test_keys_put_in_place_later_load(self):
        """Where run_docker.sh mounts your keys; one written there after the bootstrap loads too."""
        s = S("/", HOME=str(self.home), SHELL="/bin/bash", INFERPILOT_HOME="/nonexistent/inferpilot")
        with Quiet():
            self.assertEqual(bs.ensure_shell_env(s)[0], "added")
        (self.home / ".inferpilot_env").write_text("export LLM_GATEWAY_KEY=k9\n")
        out = subprocess.run(["bash", "-c", 'source "$1"; echo "$LLM_GATEWAY_KEY|$ANTHROPIC_AUTH_TOKEN"', "_", str(self.rc)],
                             env={"PATH": "/usr/bin:/bin"}, capture_output=True, text=True).stdout
        self.assertEqual(out.strip(), "k9|k9")

    @unittest.skipUnless(shutil.which("bash"), "needs bash")
    @unittest.skipIf(os.geteuid() == 0, "as root, what the test makes belongs to root, not to a user as on a host mount")
    def test_an_amd_gateway_key_points_claude_code_at_the_gateway(self):
        host = self.home / "u"
        co = make_checkout(host / "inferpilot")
        make_exe(host / ".local" / "bin" / "claude")
        (host / ".inferpilot_env").write_text("export LLM_GATEWAY_KEY=k123\n")
        s = S("/", HOME=str(self.home), SHELL="/bin/bash", INFERPILOT_HOME=str(co))
        with Quiet():
            self.assertEqual(bs.ensure_shell_env(s)[0], "added")
        show = 'source "$1"; echo "$ANTHROPIC_BASE_URL|$ANTHROPIC_CUSTOM_HEADERS|$ANTHROPIC_AUTH_TOKEN"'
        out = subprocess.run(["bash", "-c", show, "_", str(self.rc)], env={"PATH": "/usr/bin:/bin"},
                             capture_output=True, text=True).stdout
        self.assertEqual(out.strip(), "%s|Ocp-Apim-Subscription-Key: k123|k123" % bs.AMD_GATEWAY_URL)
        out = subprocess.run(["bash", "-c", show, "_", str(self.rc)], capture_output=True, text=True,
                             env={"PATH": "/usr/bin:/bin", "ANTHROPIC_BASE_URL": "https://mine"}).stdout
        self.assertEqual(out.strip(), "https://mine||", "a gateway already chosen is kept")
        (host / ".inferpilot_env").write_text("export ANTHROPIC_API_KEY=sk-test\n")
        out = subprocess.run(["bash", "-c", show, "_", str(self.rc)], env={"PATH": "/usr/bin:/bin"},
                             capture_output=True, text=True).stdout
        self.assertEqual(out.strip(), "||", "without the gateway key nothing changes")
        self.assertTrue(bs.api_configured({"LLM_GATEWAY_KEY": "k"}, str(self.home)))


class ClaudeConfigTest(unittest.TestCase):
    """Root's Claude Code is new in every container: its first-start questions are answered in its config."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root_home = Path(tmp.name) / "root"
        self.root_home.mkdir()
        self.s = S("/", HOME=str(self.root_home), INFERPILOT_HOME="/workspace/inferpilot")
        self.config = self.root_home / ".claude.json"
        self.user_settings = self.root_home / ".claude" / "settings.json"

    def read(self):
        return json.loads(self.config.read_text())

    def test_answered_once_then_kept(self):
        with Quiet():
            self.assertEqual(bs.ensure_claude_config(self.s, env={}), ("added", str(self.config)))
            self.assertEqual(bs.ensure_claude_config(self.s, env={})[0], "kept")
        data = self.read()
        self.assertTrue(data["hasCompletedOnboarding"] and data["bypassPermissionsModeAccepted"])
        self.assertTrue(data["projects"]["/workspace/inferpilot"]["hasTrustDialogAccepted"])
        self.assertNotIn("customApiKeyResponses", data, "no Anthropic API key, nothing to approve")
        self.assertEqual(stat.S_IMODE(os.stat(self.config).st_mode), 0o600)
        self.assertEqual(json.loads(self.user_settings.read_text()), {"skipDangerousModePermissionPrompt": True},
                         "the bypass permissions dialog reads the user settings")

    def test_what_is_there_is_kept(self):
        self.config.write_text(json.dumps({"userID": "u1", "projects": {"/other": {"allowedTools": ["x"]}},
                                           "customApiKeyResponses": {"approved": ["old"], "rejected": ["bad"]}}))
        with Quiet():
            self.assertEqual(bs.ensure_claude_config(self.s, env={"ANTHROPIC_API_KEY": "sk-ant-" + "k" * 40})[0], "added")
        data = self.read()
        self.assertEqual((data["userID"], data["projects"]["/other"]), ("u1", {"allowedTools": ["x"]}))
        self.assertEqual(data["customApiKeyResponses"], {"approved": ["old", "k" * 20], "rejected": ["bad"]})

    def test_an_api_key_is_approved_by_its_last_20_characters(self):
        key = "sk-ant-api03-" + "0123456789" * 3
        (self.root_home / ".inferpilot_env").write_text('export ANTHROPIC_API_KEY="%s"\n' % key)
        with Quiet():
            bs.ensure_claude_config(self.s, env={})
        self.assertEqual(self.read()["customApiKeyResponses"]["approved"], [key[-20:]])
        for line in ("export ANTHROPIC_API_KEY='<your key>'\n", "export ANTHROPIC_API_KEY=$OTHER_KEY\n"):
            (self.root_home / ".inferpilot_env").write_text(line)
            self.assertEqual(bs.anthropic_api_key(self.s, env={}), "", line)

    def test_claude_config_dir_and_a_config_that_is_not_json(self):
        cfg = self.root_home / "cfg"
        with Quiet():
            self.assertEqual(bs.ensure_claude_config(self.s, env={"CLAUDE_CONFIG_DIR": str(cfg)})[0], "added")
        self.assertTrue((cfg / ".claude.json").is_file() and (cfg / "settings.json").is_file())
        self.assertFalse(self.config.exists() or self.user_settings.exists())
        self.config.write_text("not json")
        with Quiet() as q:
            self.assertEqual(bs.ensure_claude_config(self.s, env={})[0], "skipped")
        self.assertEqual(self.config.read_text(), "not json")
        self.assertIn("not a JSON object", q.err.getvalue())

    def test_a_login_or_a_subscription_token_counts_as_api_access(self):
        state = self.root_home / "state"
        state.mkdir()
        self.assertFalse(bs.api_configured({"CLAUDE_CONFIG_DIR": str(state)}, str(self.root_home)))
        self.assertTrue(bs.api_configured({"CLAUDE_CODE_OAUTH_TOKEN": "t"}, str(self.root_home)))
        (state / ".credentials.json").write_text("{}")
        self.assertTrue(bs.api_configured({"CLAUDE_CONFIG_DIR": str(state)}, str(self.root_home)))


class GitTrustTest(unittest.TestCase):
    def test_trust_added_once(self):
        s = S(tempfile.gettempdir())
        with mock.patch.object(bs, "_has", return_value=True), mock.patch.object(bs, "_out", return_value=(1, "", "")), \
                mock.patch.object(bs, "_run", return_value=OK) as run, Quiet():
            self.assertEqual(bs.ensure_git_trust(s)[0], "added")
        run.assert_called_once_with(["git", "config", "--global", "--add", "safe.directory", "*"])
        with mock.patch.object(bs, "_has", return_value=True), mock.patch.object(bs, "_out", return_value=(0, "/a\n*", "")), \
                mock.patch.object(bs, "_run") as run, Quiet():
            self.assertEqual(bs.ensure_git_trust(s)[0], "kept")
        run.assert_not_called()


@unittest.skipUnless(shutil.which("git"), "needs git")
class GitIdentityTest(unittest.TestCase):
    """Against real git with a throwaway global config; root's home holds the keys file run_docker.sh mounts."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        patch = mock.patch.dict(os.environ, {"GIT_CONFIG_GLOBAL": str(self.root / ".gitconfig"), "GIT_CONFIG_NOSYSTEM": "1"})
        patch.start()
        self.addCleanup(patch.stop)
        for v in bs.IDENTITY_VARS:
            os.environ.pop(v, None)  # restored by the patch
        self.keys = self.root / ".inferpilot_env"
        self.s = S(self.root)

    def root_identity(self):
        get = lambda k: subprocess.run(["git", "config", "--global", "--get", k], capture_output=True, text=True).stdout.strip()
        return get("user.name"), get("user.email")

    def test_root_commits_as_the_keys_file_says(self):
        self.keys.write_text('export LLM_GATEWAY_KEY=k\nexport INFERPILOT_GIT_NAME="Ada L"\nexport INFERPILOT_GIT_EMAIL=ada@example.com\n')
        with Quiet():
            self.assertEqual(bs.ensure_git_identity(self.s), ("added", "Ada L <ada@example.com>"))
            self.assertEqual(bs.ensure_git_identity(self.s)[0], "kept")
        self.assertEqual(self.root_identity(), ("Ada L", "ada@example.com"))

    def test_the_environment_counts_and_a_new_identity_replaces_the_old(self):
        self.keys.write_text("export INFERPILOT_GIT_NAME=Bob\nexport INFERPILOT_GIT_EMAIL=bob@example.com\n")
        with Quiet():
            bs.ensure_git_identity(self.s)
        with mock.patch.dict(os.environ, {"INFERPILOT_GIT_NAME": "Ada", "INFERPILOT_GIT_EMAIL": "ada@example.com"}), Quiet():
            self.assertEqual(bs.ensure_git_identity(self.s)[0], "updated")
        self.assertEqual(self.root_identity(), ("Ada", "ada@example.com"))

    def test_an_identity_root_already_has_is_kept(self):
        subprocess.run(["git", "config", "--global", "user.name", "Bob"], check=True)
        subprocess.run(["git", "config", "--global", "user.email", "bob@example.com"], check=True)
        with Quiet():
            self.assertEqual(bs.ensure_git_identity(self.s)[0], "kept")
        self.assertEqual(self.root_identity(), ("Bob", "bob@example.com"))

    def test_none_is_said_not_invented(self):
        for text in ("export LLM_GATEWAY_KEY=k\n",  # no identity at all
                     "export INFERPILOT_GIT_NAME=<your name>\nexport INFERPILOT_GIT_EMAIL=<your email>\n",  # the README's placeholders
                     "export INFERPILOT_GIT_NAME=Ada\n"):  # half of one
            self.keys.write_text(text)
            err = io.StringIO()
            with redirect_stderr(err), redirect_stdout(io.StringIO()):
                self.assertEqual(bs.ensure_git_identity(self.s)[0], "skipped", text)
            self.assertEqual(self.root_identity(), ("", ""))
            self.assertIn("INFERPILOT_GIT_EMAIL", err.getvalue())


class FrameworkTest(unittest.TestCase):
    def test_skipped_without_a_dir(self):
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.object(bs, "IMAGE_SGLANG", os.path.join(d, "no-image-checkout")), Quiet():
                self.assertEqual(bs.ensure_framework(S(d))[0], "skipped")

    def image_framework(self, d, status="", **env):
        """ensure_framework on a fake image checkout; returns (result, the git commands it ran, what it warned)."""
        image = Path(d) / "sgl-workspace" / "sglang"
        (image / ".git").mkdir(parents=True, exist_ok=True)
        answers = {"log": (0, "f3532057e1 2026-11-05", ""), "rev-parse": (0, "false", ""),
                   "remote": (0, "https://github.com/sgl-project/sglang.git", ""), "status": (0, status, "")}
        with mock.patch.object(bs, "IMAGE_SGLANG", str(image)), \
                mock.patch.object(bs, "_out", side_effect=lambda cmd: answers[cmd[3]]), \
                mock.patch.object(bs, "_run", return_value=OK) as run, Quiet() as q:
            res = bs.ensure_framework(S(d, **env))
            self.assertNotIn("framework_dir=", bs.next_steps(bs.settings({"PWD": d, "HOME": d}), {}, {"api": True},
                                                             path_env="/usr/bin"))
        self.assertEqual(res, ("kept", str(image)))
        return res, run.call_args_list, q.err.getvalue()

    def test_the_image_checkout_is_never_changed(self):
        """run_docker.sh puts it on main in a new container from the daily image; nothing else changes it."""
        with tempfile.TemporaryDirectory() as d:
            for status, env in (("", {}), ("", {"INFERPILOT_IMAGE_PINNED": "1"}),
                                (" M python/pyproject.toml\n D python/sglang/kernels/aot/pyproject_rocm.toml", {}),
                                (" M python/sglang/srt/server_args.py", {"INFERPILOT_IMAGE_PINNED": "1"})):
                with self.subTest(status=status, **env):
                    _, ran, err = self.image_framework(d, status, **env)
                    self.assertEqual(ran, [])
                    self.assertEqual("uncommitted changes" in err, bool(status), err)
            _, _, err = self.image_framework(d, " M python/pyproject.toml\n D python/sglang/kernels/aot/pyproject_rocm.toml")
            self.assertIn("(python/pyproject.toml, python/sglang/kernels/aot/pyproject_rocm.toml)", err)
            self.assertIn("a task's Setup stops on them", err)

    def test_existing_checkout_is_kept_as_it_is(self):
        with tempfile.TemporaryDirectory() as d:
            fw = Path(d) / "sglang"
            (fw / ".git").mkdir(parents=True)
            answers = {"log": (0, "abc123 2026-10-20", ""), "rev-parse": (0, "true", ""),
                       "remote": (0, "git@github.com:sgl-project/sglang.git", "")}
            with mock.patch.object(bs, "_out", side_effect=lambda cmd: answers[cmd[3]]), \
                    mock.patch.object(bs, "_run") as run, Quiet() as q:
                self.assertEqual(bs.ensure_framework(S(d, INFERPILOT_FRAMEWORK_DIR=str(fw)))[0], "kept")
            run.assert_not_called()
            self.assertIn("shallow", q.err.getvalue())
            self.assertNotIn("tracks", q.err.getvalue())

    def test_other_content_is_left_alone(self):
        with tempfile.TemporaryDirectory() as d:
            fw = Path(d) / "sglang"
            fw.mkdir()
            (fw / "notes.txt").write_text("mine")
            with mock.patch.object(bs, "_run") as run, Quiet():
                self.assertEqual(bs.ensure_framework(S(d, INFERPILOT_FRAMEWORK_DIR=str(fw)))[0], "skipped")
            run.assert_not_called()

    def test_missing_is_cloned_blobless_and_given_back(self):
        with tempfile.TemporaryDirectory() as d:
            fw = Path(d) / "src" / "sglang"
            with mock.patch.object(bs, "_has", return_value=True), mock.patch.object(bs, "give_back") as back, \
                    mock.patch.object(bs, "_run", return_value=OK) as run, Quiet():
                self.assertEqual(bs.ensure_framework(S(d, INFERPILOT_FRAMEWORK_DIR=str(fw)))[0], "created")
            run.assert_called_once_with(["git", "clone", "--filter=blob:none", bs.DEFAULT_FRAMEWORK_REPO, str(fw)])
            back.assert_called_once_with(str(fw))


class EvalscopeTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        (self.tmp / "bin").mkdir()
        self.s = dict(S(tmp.name), evalscope_venv=str(self.tmp / "evalscope"),
                      evalscope_link=str(self.tmp / "bin" / "evalscope"))
        self.exe = self.tmp / "evalscope" / "bin" / "evalscope"

    def pip_installs_it(self, cmd):
        if "pip" in cmd:
            make_exe(self.exe)
        return OK

    def test_one_on_path_is_kept(self):
        with mock.patch.object(bs.shutil, "which", return_value="/opt/venv/bin/evalscope"), \
                mock.patch.object(bs, "_run") as run, Quiet():
            self.assertEqual(bs.ensure_evalscope(self.s), ("kept", "/opt/venv/bin/evalscope"))
        run.assert_not_called()

    def test_installed_in_a_venv_of_its_own_and_linked(self):
        s = dict(self.s, evalscope_version="1.15.0")
        venv = s["evalscope_venv"]
        with mock.patch.object(bs.shutil, "which", return_value=None), \
                mock.patch.object(bs, "_run", side_effect=self.pip_installs_it) as run, Quiet() as q:
            self.assertEqual(bs.ensure_evalscope(s), ("installed", str(self.exe)))
        self.assertEqual([c[0][0] for c in run.call_args_list], [
            [sys.executable, "-m", "venv", venv],
            [os.path.join(venv, "bin", "python"), "-m", "pip", "install", "-q", "--disable-pip-version-check",
             "evalscope==1.15.0"]])
        self.assertEqual(os.readlink(s["evalscope_link"]), str(self.exe))
        self.assertIn("is not on PATH", q.err.getvalue(), "the link's directory is not on PATH here")

    def test_an_existing_venv_is_linked_not_installed_again(self):
        make_exe(self.exe)
        with mock.patch.object(bs.shutil, "which", side_effect=[None, self.s["evalscope_link"]]), \
                mock.patch.object(bs, "_run") as run, Quiet() as q:
            self.assertEqual(bs.ensure_evalscope(self.s), ("added", str(self.exe)))
        self.assertEqual(q.err.getvalue(), "")
        with mock.patch.object(bs.shutil, "which", return_value=None), Quiet():
            self.assertEqual(bs.ensure_evalscope(self.s)[0], "kept")
        run.assert_not_called()

    def test_a_failed_install_says_what_needs_it(self):
        with mock.patch.object(bs.shutil, "which", return_value=None), \
                mock.patch.object(bs, "_run", side_effect=[OK, SimpleNamespace(returncode=1)]), Quiet() as q:
            self.assertEqual(bs.ensure_evalscope(self.s), ("failed", self.s["evalscope_venv"]))
        self.assertIn("scripts/eval_accuracy.sh", q.err.getvalue())
        self.assertFalse(os.path.lexists(self.s["evalscope_link"]))

    def test_another_file_at_the_link_is_left_alone(self):
        make_exe(self.exe)
        Path(self.s["evalscope_link"]).write_text("mine\n")
        with mock.patch.object(bs.shutil, "which", return_value=None), Quiet() as q:
            self.assertEqual(bs.ensure_evalscope(self.s), ("skipped", str(self.exe)))
        self.assertEqual(Path(self.s["evalscope_link"]).read_text(), "mine\n")
        self.assertIn("EVALSCOPE=%s" % self.exe, q.err.getvalue())


def node_tarball(version, plat="linux-x64"):
    buf = io.BytesIO()
    name = "node-%s-%s" % (version, plat)
    with tarfile.open(fileobj=buf, mode="w:xz") as tar:
        for tool, body in (("node", "echo %s" % version), ("npm", "echo npm"), ("npx", "echo npx")):
            data = ("#!/bin/sh\n%s\n" % body).encode()
            info = tarfile.TarInfo("%s/bin/%s" % (name, tool))
            info.size, info.mode = len(data), 0o755
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


class NodeTest(unittest.TestCase):
    INDEX = [{"version": "v25.2.0", "lts": False, "files": ["linux-x64", "linux-arm64"]},
             {"version": "v24.11.1", "lts": "Krypton", "files": ["linux-x64", "linux-arm64"]},
             {"version": "v22.21.1", "lts": "Jod", "files": ["linux-x64"]}]

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.s = dict(S(tmp.name), node_dir=str(self.tmp / "node"), link_dir=str(self.tmp / "bin"), node_version="")
        patcher = mock.patch.object(bs, "node_platform", return_value="linux-x64")
        patcher.start()
        self.addCleanup(patcher.stop)

    def fetcher(self, version="v24.11.1", tamper=False):
        blob = node_tarball(version)
        sha = hashlib.sha256(b"x" if tamper else blob).hexdigest()
        files = {bs.NODE_DIST + "/index.json": json.dumps(self.INDEX).encode(),
                 "%s/%s/SHASUMS256.txt" % (bs.NODE_DIST, version): ("%s  node-%s-linux-x64.tar.xz\n" % (sha, version)).encode(),
                 "%s/%s/node-%s-linux-x64.tar.xz" % (bs.NODE_DIST, version, version): blob}
        return lambda url, timeout=60: files[url]

    def no_fetch(self, url, timeout=60):
        self.fail("nothing should be downloaded: %s" % url)

    def test_the_newest_lts_or_the_release_asked_for(self):
        fetch = self.fetcher()
        self.assertEqual(bs.node_release("", fetch), "v24.11.1")
        self.assertEqual(bs.node_release("22", fetch), "v22.21.1")
        self.assertEqual(bs.node_release("v25.2.0", fetch), "v25.2.0")
        self.assertEqual(bs.node_release("18", fetch), "")

    def test_a_node_20_or_newer_is_kept(self):
        with mock.patch.object(bs.shutil, "which", return_value="/usr/bin/node"), \
                mock.patch.object(bs, "node_major", return_value=22), Quiet():
            self.assertEqual(bs.ensure_node(self.s, fetch=self.no_fetch), ("kept", "/usr/bin/node"))

    def test_installed_from_a_checked_tarball_and_linked(self):
        with mock.patch.object(bs.shutil, "which", return_value=None), Quiet():
            self.assertEqual(bs.ensure_node(self.s, fetch=self.fetcher()), ("installed", "v24.11.1"))
        dest = self.tmp / "node" / "v24.11.1"
        for tool in ("node", "npm", "npx"):
            self.assertEqual(os.readlink(self.tmp / "bin" / tool), str(dest / "bin" / tool))
        self.assertEqual(bs.node_major(str(self.tmp / "bin" / "node")), 24)
        self.assertFalse((self.tmp / "node" / "v24.11.1.partial").exists())
        with mock.patch.object(bs.shutil, "which", return_value=str(self.tmp / "bin" / "node")), Quiet():
            self.assertEqual(bs.ensure_node(self.s, fetch=self.no_fetch)[0], "kept")

    def test_a_checksum_mismatch_installs_nothing(self):
        with mock.patch.object(bs.shutil, "which", return_value=None), Quiet() as q:
            self.assertEqual(bs.ensure_node(self.s, fetch=self.fetcher(tamper=True)), ("failed", "v24.11.1"))
        self.assertIn("checksum", q.err.getvalue())
        self.assertFalse((self.tmp / "node" / "v24.11.1").exists())
        self.assertFalse(os.path.lexists(self.tmp / "bin" / "node"))

    def test_a_partial_install_is_named_not_downloaded_over(self):
        (self.tmp / "node" / "v24.11.1").mkdir(parents=True)
        fetch = self.fetcher()
        with mock.patch.object(bs.shutil, "which", return_value=None), Quiet() as q:
            self.assertEqual(bs.ensure_node(self.s, fetch=lambda url, timeout=60: fetch(url) if url.endswith("index.json")
                                            else self.no_fetch(url)), ("failed", "v24.11.1"))
        self.assertIn("v24.11.1 is there without bin/node; move it away", q.err.getvalue())

    def test_another_node_at_the_link_is_left_alone(self):
        old = make_exe(self.tmp / "bin" / "node")
        with mock.patch.object(bs.shutil, "which", return_value=str(old)), \
                mock.patch.object(bs, "node_major", side_effect=lambda p: 18 if p == str(old) else 24), Quiet() as q:
            self.assertEqual(bs.ensure_node(self.s, fetch=self.fetcher())[0], "skipped")
        self.assertIn("left as it is", q.err.getvalue())
        self.assertEqual(old.read_text(), "#!/bin/sh\n")


class CursorCliTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.s = dict(S(tmp.name, HOME=tmp.name), link_dir=str(self.tmp / "bin"))

    def test_one_already_there_is_kept(self):
        exe = make_exe(self.tmp / ".local" / "bin" / "agent")
        with mock.patch.object(bs.shutil, "which", return_value=None), mock.patch.object(bs, "_run") as run, Quiet():
            self.assertEqual(bs.ensure_cursor_cli(self.s), ("kept", str(exe)))
        run.assert_not_called()

    def test_missing_is_installed_with_the_official_installer(self):
        def install(cmd, shell=False):
            if cmd[0] == "bash":
                make_exe(self.tmp / ".cursor" / "bin" / "agent")
            return OK
        with mock.patch.object(bs.shutil, "which", return_value=None), mock.patch.object(bs, "_has", return_value=True), \
                mock.patch.object(bs, "_run", side_effect=install) as run, Quiet():
            status, path = bs.ensure_cursor_cli(self.s)
        self.assertEqual(status, "installed")
        (curl, *_, script, url), (bash, ran) = [c.args[0] for c in run.call_args_list]
        self.assertEqual((curl, url, bash, ran), ("curl", bs.CURSOR_INSTALL, "bash", script), "downloaded, then run")
        self.assertFalse(os.path.exists(script), "the downloaded installer is removed")
        self.assertEqual(os.readlink(self.tmp / "bin" / "agent"), path, "linked where PATH finds it")

    def test_a_failed_download_or_installer_is_reported_as_what_it_is(self):
        for fails, said in (("curl", "could not download the Cursor CLI installer"), ("bash", "cursorapi.com")):
            run = lambda cmd, shell=False, fails=fails: SimpleNamespace(returncode=1 if cmd[0] == fails else 0)
            with mock.patch.object(bs.shutil, "which", return_value=None), mock.patch.object(bs, "_has", return_value=True), \
                    mock.patch.object(bs, "_run", side_effect=run), Quiet() as q:
                self.assertEqual(bs.ensure_cursor_cli(self.s)[0], "failed")
            self.assertIn(said, q.err.getvalue(), fails)

    def test_logged_in_by_key_or_by_status(self):
        self.assertTrue(bs.cursor_logged_in("", {"CURSOR_API_KEY": "k"}))
        self.assertFalse(bs.cursor_logged_in("", {}))
        with mock.patch.object(bs, "_out", return_value=(0, "Logged in as someone@example.com", "")):
            self.assertTrue(bs.cursor_logged_in("/x/agent", {}))
        with mock.patch.object(bs, "_out", return_value=(0, "Not logged in", "")):
            self.assertFalse(bs.cursor_logged_in("/x/agent", {}))


class ReportTest(unittest.TestCase):
    def test_check_environment(self):
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.object(bs, "_has", return_value=True), mock.patch.object(bs, "in_container", return_value=False), Quiet():
                found = bs.check_environment({"HOME": d, "GITHUB_TOKEN": "x"})
            self.assertTrue(found["git"] and found["github_token"] and found["docker"])
            self.assertFalse(found["api"])
            self.assertNotIn("repo_token", found, "GITHUB_TOKEN is the one GitHub token")
            self.assertIn("sglang", found)
            self.assertTrue(found["evalscope"])
            with mock.patch.object(bs, "_has", return_value=True), mock.patch.object(bs, "in_container", return_value=True), Quiet():
                self.assertNotIn("docker", bs.check_environment({"HOME": d}))
            self.assertTrue(bs.api_configured({"ANTHROPIC_BASE_URL": "u", "ANTHROPIC_CUSTOM_HEADERS": "h"}, d))
            (Path(d) / ".claude").mkdir()
            (Path(d) / ".claude" / ".credentials.json").write_text("{}")
            self.assertTrue(bs.api_configured({}, d))

    def test_keys_in_inferpilot_env_count_before_the_shell_loads_them(self):
        with tempfile.TemporaryDirectory() as d:
            keys = Path(d) / ".inferpilot_env"
            keys.write_text("# keys\nexport LLM_GATEWAY_KEY=0123abcd\nexport GITHUB_TOKEN='<your GitHub token>'\n")
            self.assertEqual(bs.keys_defined(str(keys)), {"LLM_GATEWAY_KEY"})
            with mock.patch.object(bs, "_has", return_value=True), mock.patch.object(bs, "in_container", return_value=True), Quiet() as q:
                found = bs.check_environment({"HOME": d}, keys_path=str(keys))
            self.assertTrue(found["api"])
            self.assertFalse(found["github_token"], "the README's placeholder is not a token")
            self.assertIn("README step 1", q.err.getvalue())
            keys.write_text("export LLM_GATEWAY_KEY=0123abcd\nexport GITHUB_TOKEN=github_pat_x\n")
            with mock.patch.object(bs, "_has", return_value=True), mock.patch.object(bs, "in_container", return_value=True), Quiet():
                self.assertTrue(bs.check_environment({"HOME": d}, keys_path=str(keys))["github_token"])
                self.assertFalse(bs.check_environment({"HOME": d})["github_token"], "without the file nothing is assumed")

    def test_next_steps_in_a_new_container(self):
        with tempfile.TemporaryDirectory() as d:
            co = make_checkout(Path(d) / "workspace" / "inferpilot")
            s = bs.settings({"PWD": "/", "HOME": d, "INFERPILOT_HOME": str(co), "INFERPILOT_FRAMEWORK_DIR": "/x/sglang"})
            with mock.patch.object(bs, "in_container", return_value=True):
                fresh = bs.next_steps(s, {"shell": "added"}, {"api": False}, path_env="/usr/bin")
                (Path(d) / ".inferpilot_env").write_text("export GITHUB_TOKEN=x\n")
                with_keys = bs.next_steps(s, {"shell": "added"}, {"api": False}, path_env="/usr/bin")
                ready = bs.next_steps(s, {"shell": "kept", "claude-config": "added"}, {"api": True}, path_env="/usr/bin")
                unanswered = bs.next_steps(s, {"shell": "kept", "claude-config": "skipped"}, {"api": True}, path_env="/usr/bin")
            self.assertIn("source %s" % os.path.join(d, ".bashrc"), fresh)
            self.assertIn("in ~/.inferpilot_env on the host (README step 1)", fresh)
            self.assertIn("for this container only in %s" % os.path.join(d, ".inferpilot_env"), fresh)
            self.assertIn("CLAUDE_CODE_OAUTH_TOKEN", fresh)
            self.assertIn("again in every new container", fresh)
            self.assertIn("in %s, which new shells load" % os.path.join(d, ".inferpilot_env"), with_keys)
            self.assertIn("cd %s && IS_SANDBOX=1 claude --dangerously-skip-permissions" % co, ready)
            self.assertIn("framework_dir=/x/sglang, gpu_ids=0\n", ready + "\n")
            self.assertNotIn("source", ready)
            self.assertNotIn("trust this folder", ready, "the config answered the first-start questions")
            self.assertIn("Yes, I trust this folder", unanswered)
            self.assertNotIn("inferpilot.js", bs.next_steps(s, {}, {"api": True}, path_env="/usr/bin", claude_only=True))
            (Path(d) / ".inferpilot_env").write_text("export LLM_GATEWAY_KEY=k\n")
            with mock.patch.object(bs, "in_container", return_value=True), mock.patch.dict(os.environ, {}, clear=True):
                unchecked = bs.next_steps(s, {}, None, path_env="/usr/bin", claude_only=True)
            self.assertNotIn("API access", unchecked, "--claude-only runs no checks; the keys file still counts")


class CursorNextStepsTest(unittest.TestCase):
    def test_the_next_steps_say_how_to_log_the_cursor_cli_in(self):
        with tempfile.TemporaryDirectory() as d:
            co = make_checkout(Path(d) / "workspace" / "inferpilot")
            s = bs.settings({"PWD": "/", "HOME": d, "INFERPILOT_HOME": str(co)})
            with mock.patch.object(bs, "in_container", return_value=True), \
                    mock.patch.object(bs, "find_cursor_cli", return_value="/x/agent"):
                with mock.patch.object(bs, "cursor_logged_in", return_value=False):
                    fresh = bs.next_steps(s, {"cursor": "installed"}, {"api": True}, path_env="/usr/bin")
                with mock.patch.object(bs, "cursor_logged_in", return_value=True):
                    ready = bs.next_steps(s, {"cursor": "kept"}, {"api": True}, path_env="/usr/bin")
                plain = bs.next_steps(s, {}, {"api": True}, path_env="/usr/bin")
            self.assertIn("NO_OPEN_BROWSER=1 agent login", fresh)
            self.assertIn("CURSOR_API_KEY", fresh)
            self.assertIn("node runtime/run.mjs inferpilot.js --backend cursor", fresh)
            self.assertNotIn("agent login", ready)
            self.assertIn("--backend cursor", ready)
            self.assertNotIn("--backend cursor", plain)

    def test_without_claude_code_the_next_steps_say_nothing_of_it(self):
        with tempfile.TemporaryDirectory() as d:
            co = make_checkout(Path(d) / "workspace" / "inferpilot")
            env = {"PWD": "/", "HOME": d, "INFERPILOT_HOME": str(co)}
            cursor_only = bs.settings(env, {"cursor": True})
            both = bs.settings(env, {"runner": True})
            with mock.patch.object(bs, "in_container", return_value=True), \
                    mock.patch.object(bs, "find_cursor_cli", return_value="/x/agent"), \
                    mock.patch.object(bs, "cursor_logged_in", return_value=False):
                alone = bs.next_steps(cursor_only, {"node": "installed", "cursor": "installed"}, {"api": False}, path_env="/usr/bin")
                runner = bs.next_steps(both, {"node": "installed"}, {"api": True}, path_env="/usr/bin")
            self.assertNotIn("Claude Code", alone)
            self.assertIn("1) Log the Cursor CLI in", alone)
            self.assertIn("Run a task on InferPilot's own runner, every role on the Cursor CLI", alone)
            self.assertNotIn("--backend claude", alone)
            self.assertIn("Start Claude Code", runner)
            self.assertIn("--backend claude", runner)
            self.assertNotIn("--backend cursor", runner)

    def test_a_key_goes_where_it_can_be_written(self):
        """run_docker.sh mounts your keys file read-only: a key then goes in yours on the host."""
        with tempfile.TemporaryDirectory() as d:
            co = make_checkout(Path(d) / "workspace" / "inferpilot")
            keys = Path(d) / ".inferpilot_env"
            keys.write_text("export GITHUB_TOKEN=t\n")
            s = bs.settings({"PWD": "/", "HOME": d, "INFERPILOT_HOME": str(co)}, {"cursor": True, "claude": True})
            real_access = os.access
            for writable in (True, False):
                with mock.patch.object(bs, "in_container", return_value=True), \
                        mock.patch.object(bs, "keys_file", return_value=str(keys)), \
                        mock.patch.object(bs.os, "access", side_effect=lambda p, m: writable if p == str(keys)
                                          else real_access(p, m)), \
                        mock.patch.object(bs, "find_cursor_cli", return_value="/x/agent"), \
                        mock.patch.object(bs, "cursor_logged_in", return_value=False):
                    out = bs.next_steps(s, {"cursor": "installed"}, {"api": False}, path_env="/usr/bin")
                if writable:
                    self.assertIn("export CURSOR_API_KEY=<key>   in %s, which new shells load" % keys, out)
                    self.assertIn("Give Claude Code API access in %s, which new shells load:" % keys, out)
                else:
                    self.assertIn("export CURSOR_API_KEY=<key>   in ~/.inferpilot_env on the host, which containers "
                                  "started after it get (%s here is a read-only copy)" % keys, out)
                    self.assertIn("Give Claude Code API access in ~/.inferpilot_env on the host", out)

    def test_without_claude_code_a_missing_keys_file_is_said_for_what_it_holds(self):
        with tempfile.TemporaryDirectory() as d:
            for opts, said in (({}, "Claude Code here has no API access"), ({"cursor": True}, "CURSOR_API_KEY")):
                s = bs.settings({"PWD": "/", "HOME": d}, opts)
                with mock.patch.object(bs, "keys_file", return_value=""), \
                        mock.patch.object(bs, "find_host_home", return_value=""), Quiet() as q:
                    self.assertEqual(bs.ensure_keys(s)[0], "skipped")
                self.assertIn(said, q.err.getvalue())


class MainTest(unittest.TestCase):
    STEPS = ["ensure_checkout", "ensure_keys", "ensure_claude_code", "ensure_path", "ensure_claude_config",
             "ensure_git_trust", "ensure_git_identity", "ensure_shell_env", "ensure_framework", "ensure_evalscope"]
    CURSOR_STEPS = ["ensure_node", "ensure_cursor_cli"]

    def run_main(self, argv, container=False, root=False, failing=None, status=None, env_extra=None):
        env = dict({k: v for k, v in os.environ.items() if not k.startswith("INFERPILOT_")}, **(env_extra or {}))
        status = dict(status or {}, **({failing: "failed"} if failing else {}))
        with ExitStack() as stack:
            stack.enter_context(mock.patch.dict(os.environ, env, clear=True))
            m = {n: stack.enter_context(mock.patch.object(bs, n, return_value=(status.get(n, "kept"), "")))
                 for n in self.STEPS + self.CURSOR_STEPS}
            stack.enter_context(mock.patch.object(bs, "cursor_logged_in", return_value=False))
            m["check_environment"] = stack.enter_context(mock.patch.object(bs, "check_environment", return_value={"api": True}))
            stack.enter_context(mock.patch.object(bs, "in_container", return_value=container))
            stack.enter_context(mock.patch.object(bs, "is_root", return_value=root))
            stack.enter_context(Quiet())
            return bs.main(argv), m

    def called(self, m):
        return [n for n in self.STEPS if m[n].called]

    def test_skip(self):
        with mock.patch.dict(os.environ, {"INFERPILOT_SKIP_BOOTSTRAP": "1"}), \
                mock.patch.object(bs, "ensure_claude_code") as ens, Quiet():
            self.assertEqual(bs.main([]), 0)
        ens.assert_not_called()

    def test_steps_follow_where_it_runs(self):
        rc, m = self.run_main(["--framework-dir", "/x/sglang"], container=True, root=True)
        self.assertEqual((rc, self.called(m)), (0, self.STEPS))
        self.assertEqual(m["ensure_framework"].call_args[0][0]["framework_dir"], "/x/sglang")
        self.assertTrue(m["check_environment"].called)
        rc, m = self.run_main([])
        self.assertEqual(self.called(m), ["ensure_checkout", "ensure_claude_code", "ensure_path", "ensure_framework"])
        rc, m = self.run_main(["--claude-only"], container=True, root=True)
        self.assertEqual(self.called(m), ["ensure_keys", "ensure_claude_code", "ensure_path", "ensure_claude_config"])
        self.assertFalse(m["check_environment"].called)
        rc, m = self.run_main(["--check"])
        self.assertTrue(m["check_environment"].called and not self.called(m))

    def test_node_and_the_cursor_cli_only_when_asked(self):
        rc, m = self.run_main([], container=True, root=True)
        self.assertFalse(m["ensure_node"].called or m["ensure_cursor_cli"].called)
        self.assertFalse(m["check_environment"].call_args[1].get("cursor"))
        for argv, env in ((["--cursor"], None), ([], {"INFERPILOT_CURSOR": "1"})):
            rc, m = self.run_main(argv, container=True, root=True, env_extra=env)
            self.assertTrue(m["ensure_node"].called and m["ensure_cursor_cli"].called, argv or env)
            self.assertTrue(m["check_environment"].call_args[1].get("cursor"))
        with self.assertRaises(SystemExit), Quiet():
            self.run_main(["--cursor", "--claude-only"], container=True, root=True)
        rc, m = self.run_main(["--claude-only"], container=True, root=True, env_extra={"INFERPILOT_CURSOR": "1"})
        self.assertTrue(m["ensure_claude_code"].called and not m["ensure_node"].called,
                        "--claude-only stays the Claude Code steps")
        rc, m = self.run_main(["--runner"], container=True, root=True)
        self.assertTrue(m["ensure_node"].called and not m["ensure_cursor_cli"].called, "--runner is Node.js alone")

    def test_claude_and_cursor_choose_what_is_installed(self):
        """Neither: Claude Code. --cursor: the Cursor CLI alone. Both: both."""
        claude_steps = ("ensure_claude_code", "ensure_path", "ensure_claude_config")
        for argv, env, claude, cursor in (([], None, True, False), (["--claude"], None, True, False),
                                          (["--cursor"], None, False, True), (["--cursor", "--claude"], None, True, True),
                                          ([], {"INFERPILOT_CURSOR": "1"}, False, True),
                                          ([], {"INFERPILOT_CURSOR": "1", "INFERPILOT_CLAUDE": "1"}, True, True)):
            with self.subTest(argv=argv, env=env):
                rc, m = self.run_main(argv, container=True, root=True, env_extra=env)
                self.assertEqual(rc, 0)
                self.assertEqual([m[n].called for n in claude_steps], [claude] * 3)
                self.assertEqual((m["ensure_node"].called, m["ensure_cursor_cli"].called), (cursor, cursor))
                self.assertTrue(m["ensure_framework"].called)
                self.assertEqual((m["check_environment"].call_args[1].get("claude"),
                                  m["check_environment"].call_args[1].get("cursor")), (claude, cursor))

    def test_a_switch_set_to_0_is_off(self):
        for value, on in (("1", True), ("yes", True), ("0", False), ("false", False), ("", False)):
            rc, m = self.run_main([], container=True, root=True, env_extra={"INFERPILOT_CURSOR": value})
            self.assertEqual(m["ensure_cursor_cli"].called, on, value)
            rc, m = self.run_main([], container=True, root=True, env_extra={"INFERPILOT_RUNNER": value})
            self.assertEqual(m["ensure_node"].called, on, value)

    def test_a_failed_step_sets_the_exit_code(self):
        self.assertEqual(self.run_main([], failing="ensure_framework")[0], 1)

    def test_without_keys_the_rest_is_set_up(self):
        """A keys file can come later; the container is ready apart from it."""
        for status in ("skipped", "found"):
            rc, m = self.run_main([], container=True, root=True, status={"ensure_keys": status})
            self.assertEqual((rc, self.called(m)), (0, self.STEPS), status)
            self.assertTrue(m["check_environment"].called)


if __name__ == "__main__":
    unittest.main()
