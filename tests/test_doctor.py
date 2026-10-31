"""R010/R038: doctor und ignore."""

import json
import os
import shutil
import unittest

from helpers import ProjectCase


class Doctor(ProjectCase):

    def setUp(self):
        super().setUp()
        self.write("app.py", "x = 1\n")
        self.write("broken.py", "def f(:\n")
        self.write("Modul.bas", b"MsgBox \"Gr\xfc\xdfe\"\r\n", binary=True)
        self.write("mixed.txt", b"a\r\nb\n", binary=True)
        self.write(".patcherignore", ".DS_Store\nbuild/\n")
        self.make_target()

    def doctor(self):
        code = self.run_cli("doctor", "--json", "-p", self.target)
        checks = dict((c["name"], c) for c in json.loads(self.last_out)["checks"])
        return code, checks

    def test_findings(self):
        code, checks = self.doctor()
        self.assertEqual(code, 0, checks)
        self.assertEqual(checks["Journal"]["status"], "ok")
        self.assertIn("broken.py", checks["Syntax"]["message"])
        self.assertIn("Modul.bas", checks["Kodierung"]["message"])
        self.assertIn("mixed.txt", checks["Zeilenenden"]["message"])
        self.assertIn(".DS_Store", checks["Ignore-Regeln"]["message"])

    def test_errors_exit_7(self):
        draft = self.write("d.ppatch", "#PPATCH 1 p 1/1\nTITLE t\nREP app.py @1\n@1 .\nx = 2\n@.\n",
                           root=self.tmp)
        self.assertEqual(self.run_cli("run", "-y", "-q", draft, "-p", self.target), 0)
        shutil.rmtree(os.path.join(self.target, ".patcher", "backups", "p"))
        with open(os.path.join(self.target, ".patcher", "txn.json"), "w") as handle:
            json.dump({"kind": "apply", "id": "x", "files": {}}, handle)
        code, checks = self.doctor()
        self.assertEqual(code, 7)
        self.assertEqual(checks["Transaktion"]["status"], "error")
        self.assertEqual(checks["Sicherungen"]["status"], "error")

    def test_without_journal(self):
        shutil.rmtree(os.path.join(self.target, ".patcher"))
        code, checks = self.doctor()
        self.assertEqual((code, checks["Journal"]["status"]), (0, "warn"))

    @unittest.skipIf(not hasattr(os, "symlink") or os.name == "nt", "ohne symbolische Links")
    def test_symlinks(self):
        os.symlink(self.tmp, os.path.join(self.target, "link"))
        code, checks = self.doctor()
        self.assertIn("link", checks["Links"]["message"])

    def test_ignore_explain_and_list(self):
        self.assertEqual(self.run_cli("ignore", "--json", "-p", self.target, ".DS_Store",
                                      "app.py", "sub/__pycache__/x.pyc"), 0)
        paths = dict((p["path"], p) for p in json.loads(self.last_out)["paths"])
        self.assertEqual([r["source"] for r in paths[".DS_Store"]["rules"]],
                         ["Vorgabe", ".patcherignore Zeile 1"])
        self.assertTrue(paths["app.py"]["managed"])
        self.assertFalse(paths["app.py"]["ignored"])
        self.assertTrue(paths["sub/__pycache__/x.pyc"]["ignored"])
        self.assertEqual(self.run_cli("ignore", "--json", "-p", self.target), 0)
        rules = dict((r["pattern"], r["state"]) for r in json.loads(self.last_out)["rules"]
                     if r["source"] != "Vorgabe")
        self.assertEqual(rules, {".DS_Store": "ueberfluessig (schon Vorgabe)",
                                 "build/": "trifft derzeit keine Datei"})


class DirectoryPatterns(ProjectCase):

    def test_trailing_slash(self):
        self.write("build/out.o", "x")
        self.write("src/build/gen.c", "x")
        self.write("build.txt", "x")
        self.write(".patcherignore", "build/\n")
        self.make_target()
        self.run_cli("status", "-p", self.target)
        self.assertIn("1 verwaltet", self.last_out)
