import os
import re
import shutil

from helpers import ProjectCase

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROJECT = os.path.join(ROOT, "examples", "ki", "projekt")

# Beispiel -> (Vorgaben fuer INPUT, Datei, erwartete Inhalte)
EXPECT = {
    1: ((), "app.py", (b"word = word.lower()", b"def remove(self, index):")),
    2: (("dunkel=ja",), "static/style.css", (b"background: #111;",)),
    3: ((), "templates/index.html", (b'<form id="search">', b'<footer id="bottom">')),
    4: ((), "config.json", (b'"port": 8080', b'"export"', b'"debug": false')),
    5: ((), "native/calc.c", (b"return 0;", b"int chars;")),
    6: ((), "export.py", (b"def export(notes, path):", b"    return len(notes)\n")),
    7: ((), "static/app.js", (b"    return app.refresh();\n  },\n\n  clear(app) {\n",)),
}


def examples():
    with open(os.path.join(ROOT, "ki-beispiele.txt"), encoding="utf-8") as handle:
        text = handle.read()
    found = re.findall(r"=== BEISPIEL (\d+): .*?===\n(.*?)=== ENDE ===", text, re.S)
    return {int(n): body.split("Antwort", 1)[1] for n, body in found}


class KiBeispiele(ProjectCase):
    """Alle Beispiele aus ki-beispiele.txt muessen so funktionieren, wie sie
    dort stehen (inklusive Chat-Text und Code-Zaeunen)."""

    def test_all_examples_present(self):
        self.assertEqual(sorted(examples()), sorted(EXPECT))

    def test_examples(self):
        for number, answer in sorted(examples().items()):
            with self.subTest(beispiel=number):
                presets, rel, expected = EXPECT[number]
                if os.path.exists(self.target):
                    shutil.rmtree(self.target)
                shutil.copytree(PROJECT, self.target)
                self.assertEqual(self.run_cli("init", "-q", "-p", self.target), 0)
                draft = self.write("antwort.ppatch", answer, root=self.tmp)
                argv = ["run", "-y", draft, "-p", self.target]
                for preset in presets:
                    argv[1:1] = ["--set", preset]
                self.assertEqual(self.run_cli(*argv), 0, self.last_err)
                content = self.read(rel)
                for piece in expected:
                    self.assertIn(piece, content)
                self.assertNotIn(b"```", content)
                self.assertEqual(self.run_cli("rollback", "-y", "-q", "-p", self.target), 0,
                                 self.last_err)
                self.assertEqual(self.run_cli("verify", "-p", self.target), 0, self.last_err)

    def test_sealed_package_from_chat(self):
        """Ein versiegelter Patch in zwei Paketen, aus dem Chat kopiert."""
        shutil.copytree(PROJECT, self.target)
        self.assertEqual(self.run_cli("init", "-q", "-p", self.target), 0)
        draft = self.write("antwort.ppatch", examples()[1], root=self.tmp)
        sealed = os.path.join(self.tmp, "s.ppatch")
        self.assertEqual(self.run_cli("seal", draft, "--base", PROJECT, "-o", sealed,
                                      "--max-size", "400"), 0, self.last_err)
        packages = sorted(f for f in os.listdir(self.tmp) if f.startswith("s.") and
                          f.endswith(".ppatch") and f != "s.ppatch")
        self.assertGreater(len(packages), 1)
        chat = []
        for i, name in enumerate(packages):
            with open(os.path.join(self.tmp, name), encoding="utf-8") as handle:
                chat.append("Teil %d:\n```\n%s```\nweiter?\n" % (i + 1, handle.read()))
        pasted = self.write("chat.ppatch", "\n".join(chat), root=self.tmp)
        self.assertEqual(self.run_cli("apply", "-y", pasted, "-p", self.target), 0, self.last_err)
        self.assertIn(b"def remove(self, index):", self.read("app.py"))

    def test_patch_containing_patches(self):
        """Dateien, die selbst Patches enthalten (wie ki-beispiele.txt),
        lassen sich als Patch uebertragen - auch in Paketen."""
        with open(os.path.join(ROOT, "ki-beispiele.txt"), "rb") as handle:
            content = handle.read()
        self.write("README.md", "x\n")
        new = os.path.join(self.tmp, "neu")
        shutil.copytree(self.base, new)
        self.write("ki-beispiele.txt", content.decode("utf-8"), root=new)
        self.make_target()
        for extra in ((), ("--max-size", "1500")):
            out = os.path.join(self.tmp, "k%d.ppatch" % len(extra))
            self.assertEqual(self.run_cli("create", self.base, new, "-o", out, "--id", "k",
                                          "--title", "t", *extra), 0, self.last_err)
            self.assertEqual(self.run_cli("check", out if not extra else
                                          out[:-7] + ".1.ppatch", "-p", self.target), 0,
                             self.last_err)
        self.assertEqual(self.run_cli("apply", "-y", out[:-7] + ".2.ppatch", "-p", self.target), 0,
                         self.last_err)
        self.assertEqual(self.read("ki-beispiele.txt"), content)
