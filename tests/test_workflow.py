import json
import os
import shutil

from helpers import ProjectCase


class IgnoreRules(ProjectCase):
    """Ignore-Regeln: Vorgaben, nachtraegliche Aenderungen, init --force."""

    def setUp(self):
        super().setUp()
        self.write("app.py", "x = 1\n")
        self.write("notes.txt", "hallo\n")

    def make_target(self):
        shutil.copytree(self.base, self.target)
        self.assertEqual(self.run_cli("init", "-p", self.target), 0, self.last_err)

    def test_defaults(self):
        for name in (".DS_Store", "Thumbs.db", "desktop.ini", "sub/.DS_Store", "p.ppatch",
                     ".patcherignore"):
            self.write(name, "x\n")
        self.make_target()
        self.assertIn("2 Dateien verwaltet", self.last_out)
        self.write("neu.ppatch", "y\n", root=self.target)
        self.write(".DS_Store", "anders\n", root=self.target)
        self.assertEqual(self.run_cli("verify", "-p", self.target), 0, self.last_out)

    def test_patcherignore_never_managed(self):
        self.write(".patcherignore", "!.patcherignore\n")
        self.make_target()
        self.assertIn("2 Dateien verwaltet", self.last_out)

    def test_negation_restores_default(self):
        self.write("beispiel.ppatch", "#PPATCH\n")
        self.write(".patcherignore", "!*.ppatch\n")
        self.make_target()
        self.assertIn("3 Dateien verwaltet", self.last_out)

    def test_ignore_added_later(self):
        self.make_target()
        self.write(".patcherignore", "notes.txt\n", root=self.target)
        self.write("notes.txt", "geaendert\n", root=self.target)
        self.assertEqual(self.run_cli("verify", "-p", self.target), 0, self.last_out)
        self.assertIn("notes.txt: wird jetzt ignoriert", self.last_out)
        self.run_cli("status", "-p", self.target)
        self.assertIn("1 verwaltet", self.last_out)

    def test_unignored_file_reported(self):
        self.write(".patcherignore", "notes.txt\n")
        self.make_target()
        os.remove(os.path.join(self.target, ".patcherignore"))
        self.assertEqual(self.run_cli("verify", "-p", self.target), 3)
        self.assertIn("notes.txt: neue Datei", self.last_out)

    def test_adopt_with_rep(self):
        self.write(".patcherignore", "notes.txt\n")
        self.make_target()
        os.remove(os.path.join(self.target, ".patcherignore"))
        draft = self.write("d.ppatch", "#PPATCH 1 rep 1/1\nTITLE t\nREP notes.txt @1\n"
                           "@1 .\nneu\n@.\n", root=self.tmp)
        self.assertEqual(self.run_cli("run", "-y", draft, "-p", self.target), 0, self.last_err)
        self.assertEqual(self.run_cli("verify", "-p", self.target), 0, self.last_out)

    def test_init_force(self):
        self.make_target()
        self.assertEqual(self.run_cli("init", "-p", self.target), 4)
        self.assertIn("init --force", self.last_err)
        self.write(".patcherignore", "notes.txt\n", root=self.target)
        self.assertEqual(self.run_cli("init", "--force", "-p", self.target), 0, self.last_err)
        self.assertIn("neu angelegt", self.last_out)
        self.assertIn("1 Dateien verwaltet", self.last_out)

    def test_init_force_refused_with_patches(self):
        self.make_target()
        draft = self.write("d.ppatch", "#PPATCH 1 p1 1/1\nTITLE t\nADD neu.txt @1\n"
                           "@1 .\nx\n@.\n", root=self.tmp)
        self.assertEqual(self.run_cli("run", "-y", draft, "-p", self.target), 0, self.last_err)
        self.assertEqual(self.run_cli("init", "--force", "-p", self.target), 4)
        self.assertIn("ohne angewendete Patches", self.last_err)
        self.assertTrue(os.path.isfile(os.path.join(self.target, "neu.txt")))


class RunWithTests(ProjectCase):
    """run/apply --test: ein Befehl je Patch (Pruefen, Anwenden, Journal, Tests)."""

    PASS = 'python3 -c "import app; assert app.x == 2"'

    def setUp(self):
        super().setUp()
        self.write("app.py", "x = 1\n")
        self.make_target()
        self.good = self.write("g.ppatch", "#PPATCH 1 gut 1/1\nTITLE t\nREP app.py @1\n"
                               "@1 .\nx = 2\n@.\n", root=self.tmp)
        self.bad = self.write("b.ppatch", "#PPATCH 1 schlecht 1/1\nTITLE t\nREP app.py @1\n"
                              "@1 .\nx = 3\n@.\n", root=self.tmp)

    def test_quiet_summary(self):
        self.assertEqual(self.run_cli("run", "-y", "-q", "--test", self.PASS, self.good,
                                      "-p", self.target), 0, self.last_err)
        self.assertEqual(self.last_out.strip(), "OK gut | 1 Datei | Journal OK | Tests OK")

    def test_failure_keeps_patch(self):
        self.assertEqual(self.run_cli("run", "-y", "-q", "--test", self.PASS, self.bad,
                                      "-p", self.target), 6)
        self.assertIn("FEHLER schlecht", self.last_out)
        self.assertIn("bleibt angewendet", self.last_err)
        self.assertEqual(self.read("app.py"), b"x = 3\n")

    def test_rollback_on_failure(self):
        self.assertEqual(self.run_cli("run", "-y", "-q", "--test", self.PASS,
                                      "--rollback-on-failure", self.bad, "-p", self.target), 6)
        self.assertIn("zurueckgenommen", self.last_out)
        self.assertEqual(self.read("app.py"), b"x = 1\n")
        self.assertEqual(self.run_cli("status", "-p", self.target), 0)
        self.assertIn("0 angewendet", self.last_out)

    def test_config_test_and_no_test(self):
        config = os.path.join(self.target, ".patcher", "config.json")
        with open(config, "w", encoding="utf-8") as handle:
            json.dump({"ignore": [], "test": self.PASS}, handle)
        self.assertEqual(self.run_cli("run", "-y", "-q", "--no-test", self.bad,
                                      "-p", self.target), 0, self.last_err)
        self.assertEqual(self.run_cli("rollback", "-y", "-q", "-p", self.target), 0)
        self.assertEqual(self.run_cli("apply", "-y", "-q", self.seal_good(), "-p", self.target),
                         0, self.last_err)
        self.assertIn("Tests OK", self.last_out)

    def seal_good(self):
        sealed = os.path.join(self.tmp, "sealed.ppatch")
        self.assertEqual(self.run_cli("seal", self.good, "--base", self.base, "-o", sealed), 0,
                         self.last_err)
        return sealed

    def test_tests_may_create_files(self):
        command = 'python3 -c "open(\'ausgabe.log\', \'w\').write(\'x\')"'
        self.assertEqual(self.run_cli("run", "-y", "-q", "--test", command, self.good,
                                      "-p", self.target), 0, self.last_err)
