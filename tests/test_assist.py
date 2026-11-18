"""0.0.4: Arbeit mit einer KI ohne Dateiaustausch - benannte Datenbloecke,
Eingabe/Zwischenablage, handshake, context, tree, find, source, report."""

import json
import os
import zipfile
from unittest import mock

from helpers import ProjectCase

from pypatcher import assist

RECHNER = ("def berechnen(a, b):\n    ergebnis = a + b\n    return ergebnis\n\n\n"
           "class Rechner:\n    def mal(self, a, b):\n        return a * b\n")
NAMED = ("#PPATCH 1 0001-fix 1/1\nTITLE t\n\nFILE src/rechner.py\n"
         "  TEXT scope=berechnen @alt @neu\n\n@alt .\n    ergebnis = a + b\n@.\n\n"
         "@neu .\n    ergebnis = a * b\n@.\n")


class Base(ProjectCase):

    def setUp(self):
        super().setUp()
        self.write("src/rechner.py", RECHNER)
        self.write("web/app.js", "function start() {\n  return 1;\n}\n")
        self.make_target()

    def cli(self, *argv, **kw):
        return self.run_cli(*(argv + ("-p", self.target)), **kw)


class NamedBlocks(Base):
    """Beispiel aus dem Praxistest (ChatGPT/Copilot): @alt/@neu statt @1/@2."""

    def test_named_blocks(self):
        draft = self.write("d.ppatch", NAMED, root=self.tmp)
        self.assertEqual(self.cli("run", "-y", "-q", draft), 0, self.last_err)
        self.assertIn(b"ergebnis = a * b", self.read("src/rechner.py"))

    def test_sealed_with_numbers(self):
        draft = self.write("d.ppatch", NAMED, root=self.tmp)
        out = os.path.join(self.tmp, "s.ppatch")
        self.assertEqual(self.run_cli("seal", "-q", draft, "--base", self.target, "-o", out), 0)
        with open(out, encoding="utf-8") as handle:
            text = handle.read()
        self.assertIn(" @1 @2\n", text)
        self.assertNotIn("@alt", text)

    def test_unknown_name_explained(self):
        draft = self.write("d.ppatch", NAMED.replace("@alt @neu", "@alt @neux"), root=self.tmp)
        self.assertEqual(self.cli("run", "-n", draft), 2)
        self.assertIn("2 Datenblock/-bloecke erwartet (alt neu), 1 angegeben - @neux ist kein "
                      "Datenblock", self.last_err)

    def test_header_reserved(self):
        draft = self.write("d.ppatch", NAMED.replace("@alt", "@header"), root=self.tmp)
        self.assertEqual(self.cli("run", "-n", draft), 2)
        self.assertIn("@header ist als Blockname nicht erlaubt", self.last_err)

    def test_vba_header_unit_still_unit(self):
        self.write("Modul1.bas", 'Attribute VB_Name = "Modul1"\r\nSub A()\r\nEnd Sub\r\n',
                   root=self.target)
        draft = self.write("d.ppatch", "#PPATCH 1 v 1/1\nTITLE t\nFILE Modul1.bas\n"
                           "  UNIT @header @1\n@1 .\nAttribute VB_Name = \"Modul2\"\n@.\n",
                           root=self.tmp)
        self.assertEqual(self.cli("run", "-n", draft), 0, self.last_err)


class Input(Base):

    def test_stdin(self):
        self.assertEqual(self.cli("run", "-", "-y", "-q", stdin=NAMED), 0, self.last_err)
        self.assertIn(b"a * b", self.read("src/rechner.py"))

    def test_clipboard_with_chat_text(self):
        text = "Hier der Patch:\n```\n" + NAMED + "```\nViel Erfolg!"
        with mock.patch.object(assist, "paste", return_value=text):
            self.assertEqual(self.cli("run", "--clipboard", "-y", "-q"), 0, self.last_err)
        self.assertIn(b"a * b", self.read("src/rechner.py"))

    def test_clipboard_without_patch(self):
        with mock.patch.object(assist, "paste", return_value="nur Text"):
            self.assertEqual(self.cli("check", "--clipboard"), 2)
        self.assertIn("Zwischenablage enthaelt keinen pypatcher-Patch", self.last_err)

    def test_missing_input(self):
        self.assertEqual(self.cli("check"), 1)
        self.assertIn("Patchdatei fehlt", self.last_err)

    def test_clipboard_tool_missing(self):
        with mock.patch.object(assist.shutil, "which", return_value=None):
            self.assertEqual(self.cli("check", "--clipboard"), 7)
        self.assertIn("kein Zugriff auf die Zwischenablage", self.last_err)

    def test_copy_output(self):
        copied = []
        with mock.patch.object(assist, "copy", copied.append):
            self.assertEqual(self.cli("tree", "--copy", "--color", "always"), 0)
        self.assertEqual(copied, [self.last_out])
        self.assertIn("src/\n  rechner.py\n", copied[0])
        self.assertIn("in der Zwischenablage", self.last_err)


class Queries(Base):

    def test_tree(self):
        self.write(".DS_Store", "x", root=self.target)
        self.assertEqual(self.cli("tree", "src"), 0)
        self.assertEqual(self.last_out.splitlines()[:2], ["src/", "  rechner.py"])
        self.assertNotIn("app.js", self.last_out)
        self.cli("tree")
        self.assertNotIn(".DS_Store", self.last_out)

    def test_context(self):
        self.assertEqual(self.cli("context"), 0)
        self.assertIn("PYPATCH-CONTEXT/1", self.last_out)
        self.assertIn("Journal: 0 Patches angewendet, Ist-Stand OK", self.last_out)
        self.assertIn("src/rechner.py  python, 8 Zeilen  [berechnen,", self.last_out)
        self.assertIn("Rechner.mal]", self.last_out)

    def test_find(self):
        self.assertEqual(self.cli("find", "mal", "start"), 0)
        self.assertIn("src/rechner.py::Rechner.mal  (method, Zeilen 7-8)", self.last_out)
        self.assertIn("web/app.js::start", self.last_out)
        self.assertEqual(self.cli("find", "MAL", "nix"), 3)
        self.assertIn("Schreibweise abweichend", self.last_out)
        self.assertIn("nix: nicht gefunden", self.last_out)

    def test_source(self):
        self.assertEqual(self.cli("source", "src/rechner.py::Rechner.mal", "web/app.js:2-3"), 0)
        self.assertIn("=== src/rechner.py::Rechner.mal (method, Zeilen 7-8) ===\n```python\n"
                      "    def mal(self, a, b):\n        return a * b\n```", self.last_out)
        self.assertIn("```javascript\n  return 1;\n}\n```", self.last_out)

    def test_source_truncated_and_unknown_unit(self):
        self.assertEqual(self.cli("source", "src/rechner.py", "--max-lines", "2"), 0)
        self.assertIn("(gekuerzt: 2 von 8 Zeilen - Rest mit src/rechner.py:3-8)", self.last_out)
        self.assertEqual(self.cli("source", "src/rechner.py::mul"), 3)

    def test_source_stays_in_project(self):
        for target in ("../x.py", ".git/config", ".patcher/state.json", "/etc/passwd"):
            self.assertEqual(self.cli("source", target), 1, target)

    def test_source_no_symlinks(self):
        if not hasattr(os, "symlink") or os.name == "nt":
            self.skipTest("ohne symbolische Links")
        os.symlink(self.tmp, os.path.join(self.target, "link"))
        self.assertEqual(self.cli("source", "link/base/src/rechner.py"), 1)
        self.assertIn("symbolischen Link", self.last_err)
        self.cli("tree")
        self.assertNotIn("link", self.last_out)

    def test_json(self):
        self.assertEqual(self.cli("find", "mal", "--json"), 0)
        self.assertIn("src/rechner.py::Rechner.mal  (method, Zeilen 7-8)",
                      json.loads(self.last_out)["find"][1])


class Handshake(Base):

    def test_prompt_and_commands(self):
        with mock.patch.object(assist.sys, "argv", ["tools/pp/patcher.pyz"]):
            self.assertEqual(self.run_cli("handshake", "-p", "neu"), 0)   # auch ohne Projekt
            self.assertNotIn("\nProjekt: ", self.last_out)
            self.assertEqual(self.cli("handshake"), 0, self.last_err)
        text = self.last_out
        self.assertTrue(text.startswith("PYPATCH-HANDSHAKE/1\npypatcher "))
        self.assertIn("1. GRUNDFORM", text)
        self.assertNotIn("10. WENN DU CODE AUSFUEHREN KANNST", text)
        self.assertIn("python3 tools/pp/patcher.pyz run --clipboard -p " + self.target, text)
        self.assertNotIn("python3 patcher.py", text)
        self.assertNotIn("<projekt>", text)
        self.assertIn("Projekt: 2 Dateien (js 1, python 1)", text)
        self.assertIn("PYPATCH/1 BEREIT - Spezifikation ", text)
        self.cli("handshake", "--full")
        self.assertIn("10. WENN DU CODE AUSFUEHREN KANNST", self.last_out)

    def test_bundle_contains_prompt(self):
        out = os.path.join(self.tmp, "p.pyz")
        self.assertEqual(self.run_cli("bundle", "-o", out), 0)
        with zipfile.ZipFile(out) as archive:
            self.assertIn("pypatcher/ki-prompt.txt", archive.namelist())


class Report(Base):

    def failing(self):
        return self.write("d.ppatch", NAMED.replace("ergebnis = a + b", "ergebnis = a - b"),
                          root=self.tmp)

    def test_report_after_failure(self):
        self.assertEqual(self.cli("run", "-y", self.failing()), 3)
        self.assertEqual(self.cli("report"), 0)
        text = self.last_out
        self.assertIn("PYPATCH-REPORT/1\nPatch: 0001-fix | Befehl: run", text)
        self.assertIn("FEHLER: Datei 1 (src/rechner.py), Aenderung 1", text)
        self.assertIn("Betroffen: src/rechner.py, Aenderung 1 (replace_text) in berechnen", text)
        self.assertIn("```python\ndef berechnen(a, b):\n    ergebnis = a + b\n", text)

    def test_report_from_stdin_and_cleared(self):
        bad = NAMED.replace("ergebnis = a + b", "ergebnis = a - b")
        self.assertEqual(self.cli("run", "-n", "-", stdin=bad), 3)
        self.cli("report")
        self.assertIn("Patch: 0001-fix | Befehl: run", self.last_out)
        self.assertEqual(self.cli("run", "-", "-y", "-q", stdin=NAMED), 0)
        self.cli("report")
        self.assertIn("Kein gemerkter Fehler", self.last_out)

    def test_report_invalid_patch(self):
        self.assertEqual(self.cli("check", "-", stdin="#PPATCH 1 x 1/1\nTITLE t\nFOO a\n"), 2)
        self.cli("report")
        self.assertIn("Der Patch selbst ist fehlerhaft", self.last_out)

    def test_report_large_file_lists_units(self):
        body = "".join("def f%d():\n    return %d\n\n\n" % (i, i) for i in range(120))
        self.write("big.py", body, root=self.target)
        draft = self.write("b.ppatch", "#PPATCH 1 b 1/1\nTITLE t\nFILE big.py\n"
                           "  TEXT @1 @2\n@1 .\nreturn 999\n@.\n@2 .\nreturn 0\n@.\n",
                           root=self.tmp)
        self.assertEqual(self.cli("run", "-y", draft), 3)
        self.cli("report")
        self.assertIn("  f119  (function, Zeilen", self.last_out)
        self.assertIn("Ausschnitt mit 'source big.py:VON-BIS'", self.last_out)

    def test_without_journal(self):
        os.remove(os.path.join(self.target, ".patcher", "state.json"))
        self.assertEqual(self.cli("report"), 4)


class Clipboard(ProjectCase):
    """Werkzeugwahl je System (ohne echte Zwischenablage)."""

    def test_tools_and_errors(self):
        calls = []

        def run(cmd, **kw):
            calls.append(cmd[0])
            if cmd[0] == "wl-paste":
                return mock.Mock(returncode=1, stdout=b"", stderr=b"no wayland")
            return mock.Mock(returncode=0, stdout=b"a\r\nb", stderr=b"")
        with mock.patch.object(assist, "_platform", return_value="linux"), \
                mock.patch.object(assist.shutil, "which", return_value="/bin/x"), \
                mock.patch.object(assist.subprocess, "run", run):
            self.assertEqual(assist.paste(), "a\nb")
        self.assertEqual(calls, ["wl-paste", "xclip"])
        with mock.patch.object(assist, "_platform", return_value="darwin"), \
                mock.patch.object(assist.shutil, "which", return_value="/bin/x"), \
                mock.patch.object(assist.subprocess, "run",
                                  return_value=mock.Mock(returncode=1, stderr=b"kaputt")):
            with self.assertRaises(assist.EnvironmentProblem) as caught:
                assist.copy("x")
        self.assertIn("pbcopy: kaputt", str(caught.exception))


if __name__ == "__main__":
    import unittest
    unittest.main()
