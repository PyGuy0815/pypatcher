"""Praxistest 7 (Grossputz eines Projekts mit MOVE/DMOVE)."""

import contextlib
import io
import os

from helpers import ProjectCase

PYC = "old/__pycache__/a.cpython-313.pyc"


class Tty(io.StringIO):
    def isatty(self):
        return True


class MovedBytecode(ProjectCase):
    """MOVE einer .py samt __pycache__: das Schreiben der .py darf den eben
    mitverschobenen Bytecode nicht als veraltet loeschen (Datenverlust)."""

    def test_move_with_pycache_and_rollback(self):
        self.write("old/a.py", "x = 1\n")
        self.make_target()
        self.write(PYC, b"PYC", root=self.target, binary=True)
        draft = self.write("d.ppatch", "#PPATCH 1 m 1/1\nTITLE t\n"
                           "MOVE old/a.py arch/a.py\n"
                           "MOVE %s arch/__pycache__/a.cpython-313.pyc\n" % PYC, root=self.tmp)
        self.assertEqual(self.run_cli("run", "-y", draft, "-p", self.target), 0, self.last_err)
        self.assertEqual(self.read("arch/__pycache__/a.cpython-313.pyc"), b"PYC")
        self.assertNotIn("jetzt ignoriert", self.last_out)
        self.assertIn("geschrieben/verschoben, aber nicht verwaltet", self.last_out)
        self.assertEqual(self.run_cli("rollback", "-y", "-q", "-p", self.target), 0)
        self.assertEqual(self.read(PYC), b"PYC")
        self.assertFalse(os.path.exists(os.path.join(self.target, "arch")))

    def test_stale_bytecode_still_dropped(self):
        self.write("a.py", "x = 1\n")
        self.make_target()
        self.write("__pycache__/a.cpython-313.pyc", b"ALT", root=self.target, binary=True)
        draft = self.write("d.ppatch", "#PPATCH 1 p 1/1\nTITLE t\nREP a.py @1\n@1 .\nx = 2\n@.\n",
                           root=self.tmp)
        self.assertEqual(self.run_cli("run", "-y", "-q", draft, "-p", self.target), 0)
        self.assertFalse(os.path.exists(
            os.path.join(self.target, "__pycache__", "a.cpython-313.pyc")))


class IgnoredInDirMove(ProjectCase):

    def test_dmove_with_ds_store(self):
        self.write("staging/a.txt", "a\n")
        self.make_target()
        self.write("staging/.DS_Store", b"\0", root=self.target, binary=True)
        draft = self.write("d.ppatch", "#PPATCH 1 m 1/1\nTITLE t\nDMOVE staging archiv\n",
                           root=self.tmp)
        self.assertEqual(self.run_cli("run", "-y", draft, "-p", self.target), 0, self.last_err)
        self.assertTrue(os.path.isfile(os.path.join(self.target, "archiv", ".DS_Store")))
        self.assertEqual(self.run_cli("verify", "-p", self.target), 0)
        self.assertNotIn("Hinweis", self.last_out)
        self.run_cli("status", "-p", self.target)
        self.assertIn("1 verwaltet", self.last_out)


class UnmanagedNotes(ProjectCase):

    def test_many_unmanaged_one_line(self):
        self.write("a.txt", "a\n")
        self.make_target()
        for n in range(5):
            self.write("neu%d.log" % n, "x", root=self.target)
        self.assertEqual(self.run_cli("verify", "-p", self.target), 0)
        self.assertEqual(self.last_out.count("Hinweis"), 1)
        self.assertIn("5 neue Dateien, nicht verwaltet: neu0.log, neu1.log, neu2.log ...",
                      self.last_out)
        self.assertEqual(self.run_cli("verify", "-v", "-p", self.target), 0)
        self.assertIn("  ? neu4.log", self.last_out)

    def test_few_unmanaged_listed(self):
        self.write("a.txt", "a\n")
        self.make_target()
        self.write("neu.log", "x", root=self.target)
        self.run_cli("verify", "-p", self.target)
        self.assertIn("neu.log: neue Datei, nicht verwaltet", self.last_out)


class PathArguments(ProjectCase):
    """ignore PFAD: relativ immer zum Projekt, nie zum aktuellen Verzeichnis."""

    def test_relative_to_project_not_cwd(self):
        self.write("app.py", "x = 1\n")
        self.make_target()
        self.write(".DS_Store", "x", root=self.tmp)
        cwd = os.getcwd()
        os.chdir(self.tmp)
        try:
            code = self.run_cli("ignore", "-p", self.target, ".DS_Store", "app.py")
        finally:
            os.chdir(cwd)
        self.assertEqual(code, 0, self.last_out + self.last_err)
        self.assertNotIn("nicht im Projekt", self.last_out + self.last_err)

    def test_outside_rejected(self):
        self.make_target()
        self.assertEqual(self.run_cli("ignore", "-p", self.target, "../x"), 1)
        self.assertIn("liegt nicht im Projekt", self.last_err)
        self.assertEqual(self.run_cli("ignore", "-p", self.target, self.tmp), 1)


class Confirmation(ProjectCase):
    """Ohne Terminal wird nicht stillschweigend zugestimmt."""

    def setUp(self):
        super().setUp()
        self.write("a.py", "x = 1\n")
        self.make_target()
        self.draft = self.write("d.ppatch", "#PPATCH 1 p 1/1\nTITLE t\nREP a.py @1\n@1 .\n"
                                "x = 2\n@.\n", root=self.tmp)

    def test_without_terminal_needs_yes(self):
        self.assertEqual(self.run_cli("run", self.draft, "-p", self.target), 1)
        self.assertIn("ohne Terminal nicht moeglich: mit -y bestaetigen", self.last_err)
        self.assertEqual(self.read("a.py"), b"x = 1\n")

    def test_terminal_answer(self):
        from unittest import mock
        import sys
        from pypatcher import cli
        for answer, code, content in (("n\n", 1, b"x = 1\n"), ("j\n", 0, b"x = 2\n")):
            with mock.patch.object(sys, "stdin", Tty(answer)), \
                    mock.patch("builtins.input", lambda prompt: sys.stdin.readline()), \
                    contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(cli.main(["run", "-q", self.draft, "-p", self.target]), code)
            self.assertEqual(self.read("a.py"), content)
