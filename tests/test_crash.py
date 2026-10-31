"""R038: Abbrueche beim Schreiben (Fehler-Injektion). Fuer jeden moeglichen
Zeitpunkt wird der Prozess hart beendet; danach muss recover einen
konsistenten Stand herstellen: entweder vollstaendig ohne oder vollstaendig
mit dem Patch, und verify muss passen."""

import os
import shutil
import subprocess
import sys

from helpers import ProjectCase

KILLER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "killer.py")

PATCH = ("#PPATCH 1 p 1/1\nTITLE t\nFILE a.py\n  TEXT @1 @2\nADD neu/b.txt @3\nDEL c.txt\n"
         "MOVE d.txt e/d.txt\n@1 .\nx = 1\n@.\n@2 .\nx = 2\n@.\n@3 .\nneu\n@.\n")


class Crash(ProjectCase):

    def setUp(self):
        super().setUp()
        self.write("a.py", "x = 1\n")
        self.write("c.txt", "c\n")
        self.write("d.txt", "d\n")
        self.make_target()
        self.draft = self.write("p.ppatch", PATCH, root=self.tmp)
        self.pristine = os.path.join(self.tmp, "pristine")
        shutil.copytree(self.target, self.pristine)

    def reset(self, source):
        shutil.rmtree(self.target)
        shutil.copytree(source, self.target)

    def killed(self, n, when, *argv):
        env = dict(os.environ, PP_KILL_AT=str(n), PP_KILL_WHEN=when)
        result = subprocess.run([sys.executable, KILLER] + list(argv), env=env,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        return result.returncode

    def files(self):
        return self.snapshot(self.target)

    def consistent(self, states, label):
        self.assertEqual(self.run_cli("recover", "-q", "-p", self.target), 0,
                         label + self.last_err)
        self.assertEqual(self.run_cli("verify", "-p", self.target), 0, label + self.last_out)
        self.run_cli("status", "-p", self.target)
        applied = 1 if "1 angewendet" in self.last_out else 0
        self.assertEqual(self.files(), states[applied], label)
        return applied

    def snapshot(self, root):
        out = {}
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d != ".patcher"]
            for name in filenames:
                path = os.path.join(dirpath, name)
                with open(path, "rb") as handle:
                    out[os.path.relpath(path, root)] = handle.read()
        return out

    def test_apply_killed_everywhere(self):
        before = self.files()
        self.assertEqual(self.run_cli("run", "-y", "-q", self.draft, "-p", self.target), 0)
        after = self.files()
        self.reset(self.pristine)
        seen = set()
        for when in ("before", "after"):
            n = 1
            while True:
                code = self.killed(n, when, "run", "-y", "-q", self.draft, "-p", self.target)
                if code != 99:
                    self.assertEqual(code, 0)
                    break
                seen.add(self.consistent({0: before, 1: after}, "apply %s %d: " % (when, n)))
                self.reset(self.pristine)
                n += 1
            self.reset(self.pristine)
        self.assertEqual(seen, {0, 1})        # beide Ausgaenge kommen vor

    def test_rollback_killed_everywhere(self):
        before = self.files()
        self.assertEqual(self.run_cli("run", "-y", "-q", self.draft, "-p", self.target), 0)
        after = self.files()
        applied = os.path.join(self.tmp, "applied")
        shutil.copytree(self.target, applied)
        for when in ("before", "after"):
            n = 1
            while True:
                code = self.killed(n, when, "rollback", "-y", "-q", "-p", self.target)
                if code != 99:
                    self.assertEqual(code, 0)
                    break
                self.consistent({0: before, 1: after}, "rollback %s %d: " % (when, n))
                self.reset(applied)
                n += 1
            self.reset(applied)
