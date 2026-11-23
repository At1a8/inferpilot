"""Nothing private reaches the public repository: no token, key or private key, no internal host or site, no
private network address, no real home or storage directory of this user, and not the name of the machine
the tests run on or of its siblings. Offline; it reads the files git tracks, and new ones it does not ignore.

    python3 -m unittest discover -s tests
"""
import getpass
import re
import socket
import subprocess
import sys
import unittest

from test_readme import ROOT

sys.path.insert(0, str(ROOT / "scripts"))
# The scripts that check what InferPilot publishes (daily_report, leak_scan) use the same patterns.
from daily_report import AMD_HOST, INTERNAL_SITE, PRIVATE_IP, PUBLIC_AMD, SECRET, machine_names  # noqa: E402

HOME = re.compile(r"/home/(?![<${*])([A-Za-z0-9._-]+)")
# Names that stand for anyone.
PLACEHOLDER_USERS = {"user", "you", "me", "alice", "bob", "username"}
GENERIC_NAMES = {"root", "runner", "user", "ubuntu", "admin", "localhost"}


def tracked():
    """The files git tracks and the new ones it does not ignore; None outside a git checkout, and git's
    own error for anything else (an owner git does not trust), rather than a quiet skip."""
    where = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--is-inside-work-tree"], capture_output=True, text=True)
    if where.returncode != 0 and "not a git repository" in where.stderr:
        return None
    out = subprocess.run(["git", "-C", str(ROOT), "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
                         capture_output=True)
    if out.returncode != 0:
        raise RuntimeError("git ls-files: %s" % out.stderr.decode(errors="replace").strip())
    return sorted(set(out.stdout.decode().split("\0")) - {""})


FILES = tracked()


def files():
    for name in FILES or []:
        path = ROOT / name
        if path.is_file():
            data = path.read_bytes()
            if b"\0" not in data:
                yield name, data.decode("utf-8", "replace")


def found(pattern, keep=lambda m: True, show=lambda s: s[:60]):
    return ["%s:%d: %s" % (name, text.count("\n", 0, m.start()) + 1, show(m.group(0)))
            for name, text in files() for m in pattern.finditer(text) if keep(m)]


@unittest.skipIf(FILES is None, "not a git checkout")
class HygieneTest(unittest.TestCase):
    def test_no_token_or_private_key(self):
        # a public CI log shows what fails: the start of a token says which it is, and no more
        self.assertEqual([], found(SECRET, show=lambda s: s[:6] + "..."))

    def test_no_internal_host_or_site(self):
        self.assertEqual([], found(AMD_HOST, lambda m: m.group(0).lower() not in PUBLIC_AMD) + found(INTERNAL_SITE))

    def test_no_private_network_address(self):
        self.assertEqual([], found(PRIVATE_IP))

    def test_no_real_home_directory(self):
        self.assertEqual([], found(HOME, lambda m: m.group(1).lower() not in PLACEHOLDER_USERS))

    def test_no_storage_directory_of_this_user(self):
        try:
            user = getpass.getuser()
        except (KeyError, OSError):  # a uid with no passwd entry and no USER
            self.skipTest("no name for uid")
        if len(user) < 3 or user.lower() in GENERIC_NAMES:
            self.skipTest("a short or generic user name: %s" % user)
        local = re.compile(r"(?<![\w.-])/(?:mnt|nfs\w*|data\d*|raid\w*|shared|scratch|apps|kmd\d*|work|export)/(?:[\w.-]+/)*?%s(?![\w.-])"
                           % re.escape(user))
        self.assertEqual([], found(local))

    def test_not_the_name_of_this_machine_or_its_siblings(self):
        host = socket.gethostname().split(".")[0]
        if len(host) < 6 or host.lower() in GENERIC_NAMES:
            self.skipTest("a short or generic machine name: %s" % host)
        self.assertEqual([], found(machine_names(host)))


if __name__ == "__main__":
    unittest.main()
