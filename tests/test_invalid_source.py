"""Ausgangsdateien mit Syntaxfehler (Praxistest: ips.py) lassen sich reparieren."""

import os
import shutil

from helpers import ProjectCase

BROKEN = 'IPS_MAGIC = "PATCH"\n\n\ndef createIPS(a, b):\n    while(p <= len(b)):\n        byte_ori = \n'
FIXED = "from formats.ips import createIPS\n"


class InvalidSource(ProjectCase):

    def setUp(self):
        super().setUp()
        self.write("ips.py", BROKEN)
        self.write("ok.py", "def f():\n    return 1\n")
        self.make_target()
        self.new = os.path.join(self.tmp, "new")
        shutil.copytree(self.base, self.new)

    def create(self, *extra):
        out = os.path.join(self.tmp, "p.ppatch")
        self.assertEqual(self.run_cli("create", self.base, self.new, "-o", out, "--id", "fix",
                                      "--title", "t", *extra), 0, self.last_err)
        return out

    def apply_and_check(self, patch, expected):
        self.assertEqual(self.run_cli("apply", "-y", "-q", patch, "-p", self.target), 0,
                         self.last_err)
        self.assertEqual(self.read("ips.py"), expected.encode())
        self.assertEqual(self.run_cli("rollback", "-y", "-q", "-p", self.target), 0)
        self.assertEqual(self.read("ips.py"), BROKEN.encode())

    def test_invalid_to_valid(self):
        self.write("ips.py", FIXED, root=self.new)
        patch = self.create()
        self.assertIn("(ips.py): Ausgangsdatei ist kein gueltiges Python", self.last_out)
        self.apply_and_check(patch, FIXED)

    def test_invalid_to_valid_level_file(self):
        self.write("ips.py", FIXED, root=self.new)
        self.apply_and_check(self.create("--level", "file"), FIXED)

    def test_invalid_to_invalid(self):
        changed = BROKEN.replace("byte_ori = ", "byte_neu = ")
        self.write("ips.py", changed, root=self.new)
        self.apply_and_check(self.create(), changed)

    def test_other_file_keeps_python_profile(self):
        self.write("ips.py", FIXED, root=self.new)
        self.write("ok.py", "def f():\n    return 2\n", root=self.new)
        with open(self.create(), encoding="utf-8") as handle:
            text = handle.read()
        self.assertIn("FILE ok.py py ", text)
        self.assertIn("REP ips.py txt ", text)

    def test_wrong_original_refused(self):
        self.write("ips.py", FIXED, root=self.new)
        patch = self.create()
        self.write("ips.py", BROKEN + "# lokal geaendert\nx = \n", root=self.target)
        self.assertEqual(self.run_cli("apply", "-y", patch, "-p", self.target), 3)
        self.assertIn("hash_before stimmt nicht", self.last_err)

    def test_draft_rep_and_text(self):
        draft = self.write("d.ppatch", "#PPATCH 1 d 1/1\nTITLE t\nFILE ips.py\n"
                           "  TEXT @1 @2\n@1 .\nbyte_ori = \n@.\n"
                           "@2 .\nbyte_ori = b[p]\np += 1\n@.\n", root=self.tmp)
        self.assertEqual(self.run_cli("run", "-y", draft, "-p", self.target), 0, self.last_err)
        self.assertIn(b"byte_ori = b[p]\n", self.read("ips.py"))

    def test_unit_command_needs_valid_file(self):
        draft = self.write("d.ppatch", "#PPATCH 1 d 1/1\nTITLE t\nFILE ips.py\n"
                           "  UNIT createIPS @1\n@1 .\ndef createIPS(a, b):\n\tpass\n@.\n",
                           root=self.tmp)
        self.assertNotEqual(self.run_cli("run", "-n", draft, "-p", self.target), 0)
        self.assertIn("Einheiten gibt es bei mode text nicht", self.last_err)


class CompareOption(ProjectCase):

    def test_create_compare_sets_default(self):
        self.write("a.py", "def f():\n    return 1\n")
        new = os.path.join(self.tmp, "new")
        shutil.copytree(self.base, new)
        self.write("a.py", "def f():\n    return 2\n", root=new)
        out = os.path.join(self.tmp, "p.ppatch")
        self.assertEqual(self.run_cli("create", self.base, new, "-o", out, "--id", "c",
                                      "--title", "t", "--compare", "text"), 0, self.last_err)
        with open(out, encoding="utf-8") as handle:
            text = handle.read()
        self.assertIn("DEFAULT txt\n", text)
        self.assertIn("FILE a.py txt ", text)
        self.make_target()
        self.assertEqual(self.run_cli("apply", "-y", "-q", out, "-p", self.target), 0,
                         self.last_err)
