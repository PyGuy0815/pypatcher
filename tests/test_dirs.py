"""R038: Verzeichnis-Befehle DMOVE, DCOPY, DDEL mit versiegelter Dateiliste."""

import os

from helpers import ProjectCase


class Directories(ProjectCase):

    def setUp(self):
        super().setUp()
        self.write("tools/old lib/a.py", "x = 1\n")
        self.write("tools/old lib/sub/b.bin", b"\x00\x01", binary=True)
        self.write("keep.txt", "k\n")
        self.make_target()

    def draft(self, line, name="d.ppatch"):
        return self.write(name, "#PPATCH 1 d 1/1\nTITLE t\n%s\n" % line, root=self.tmp)

    def exists(self, rel):
        return os.path.exists(os.path.join(self.target, *rel.split("/")))

    def seal(self, line):
        sealed = os.path.join(self.tmp, "s.ppatch")
        self.assertEqual(self.run_cli("seal", self.draft(line), "--base", self.base,
                                      "-o", sealed), 0, self.last_err)
        return sealed

    def test_move_seal_apply_rollback(self):
        sealed = self.seal('DMOVE "tools/old lib" archive/lib')
        with open(sealed, encoding="utf-8") as handle:
            text = handle.read()
        self.assertIn('DMOVE "tools/old lib" archive/lib @1\n@1 2\n', text)
        self.assertIn(" sub/b.bin\n", text)
        self.assertEqual(self.run_cli("apply", "-y", "-q", sealed, "-p", self.target), 0,
                         self.last_err)
        self.assertEqual(self.read("archive/lib/sub/b.bin"), b"\x00\x01")
        self.assertFalse(self.exists("tools"))
        self.assertEqual(self.run_cli("verify", "-p", self.target), 0, self.last_out)
        self.assertEqual(self.run_cli("rollback", "-y", "-q", "-p", self.target), 0)
        self.assertEqual(self.read("tools/old lib/a.py"), b"x = 1\n")
        self.assertFalse(self.exists("archive"))

    def test_sealed_list_detects_changes(self):
        sealed = self.seal('DMOVE "tools/old lib" archive/lib')
        self.write("tools/old lib/neu.txt", "n\n", root=self.target)
        self.write("tools/old lib/a.py", "x = 2\n", root=self.target)
        self.assertEqual(self.run_cli("apply", "-y", "--json", sealed, "-p", self.target), 3)
        self.assertIn('"extra": ["neu.txt"]', self.last_out)
        self.assertIn('"changed": ["a.py"]', self.last_out)
        self.assertTrue(self.exists("tools/old lib/a.py"))

    def test_copy_and_delete_draft(self):
        self.assertEqual(self.run_cli("run", "-y", self.draft('DCOPY "tools/old lib" kopie'),
                                      "-p", self.target), 0, self.last_err)
        self.assertEqual(self.read("kopie/a.py"), b"x = 1\n")
        self.assertTrue(self.exists("tools/old lib/a.py"))
        draft = self.write("e.ppatch", "#PPATCH 1 e 1/1\nTITLE t\nDDEL kopie\n", root=self.tmp)
        self.assertEqual(self.run_cli("run", "-y", draft, "-p", self.target), 0, self.last_err)
        self.assertFalse(self.exists("kopie"))

    def test_refusals(self):
        self.assertEqual(self.run_cli("run", "-n", self.draft("DMOVE gibtsnicht x"),
                                      "-p", self.target), 3)
        self.assertEqual(self.run_cli("run", "-n", self.draft('DMOVE tools "tools/x"'),
                                      "-p", self.target), 2)
        os.makedirs(os.path.join(self.target, "repo", ".git"))
        self.write("repo/.git/HEAD", "ref\n", root=self.target)
        self.assertEqual(self.run_cli("run", "-n", self.draft("DMOVE repo r2"),
                                      "-p", self.target), 3)
        self.assertIn("Versionsverwaltung", self.last_err)
