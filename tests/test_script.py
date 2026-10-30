"""Ablaufsteuerung (PRINT/INPUT/IF/GOTO ...) und Entwuerfe aus dem Chat (run)."""

import contextlib
import io
import json
import os
import shutil
import unittest

from helpers import ProjectCase

from pypatcher import flow, patchfile
from pypatcher.errors import PatchInvalid, UsageError

APP = "def add(a, b):\n    return a + b\n\n\ndef main():\n    print(add(1, 2))\n"
STYLE = "body {\n  color: black;\n}\n"

DRAFT = """Text vor dem Patch wird ignoriert.
```
#PPATCH 1 0001-ki 1/?
TITLE Kommazahlen, optional dunkles Design
PRINT Hinweis fuer ${user}
INPUT design "Dunkles Design?" choices=ja,nein default=nein
FILE app.py
  UNIT add @1
IF design == ja
  FILE style.css
    TEXT scope=body @2 @3
ELIF design == vielleicht
  EXIT gibt es nicht
ELSE
  PRINT Design bleibt.
END
```
Chat-Text zwischen den Teilen.
```
#PPATCH 1 0001-ki 2/2
@1 .
def add(a, b):
    return float(a) + float(b)
@.
@2 .
color: black;
@.
@3 .
color: white;
@.
```
Chat-Text danach.
"""


def parse(text):
    return patchfile.parse_body(text, "test")


class Interpreter(unittest.TestCase):
    def plan(self, text, preset=None, answers=None):
        said = []
        patch = parse(text)
        entries, got = flow.plan(patch, lambda item: (answers or {})[item["var"]],
                                 say=said.append, preset=preset)
        return [e["path"] for e in entries], got, said

    def test_branches(self):
        text = ("TITLE t\nINPUT x choices=a,b,c\nIF x == a\nDEL a.txt\nELIF x in b,c\nDEL b.txt\n"
                "IF x != c\nDEL c.txt\nEND\nELSE\nDEL d.txt\nEND\nDEL e.txt\n")
        self.assertEqual(self.plan(text, {"x": "a"})[0], ["a.txt", "e.txt"])
        self.assertEqual(self.plan(text, {"x": "b"})[0], ["b.txt", "c.txt", "e.txt"])
        self.assertEqual(self.plan(text, {"x": "c"})[0], ["b.txt", "e.txt"])

    def test_goto_set_print(self):
        text = ("TITLE t\nSET name Welt\nGOTO weiter\nDEL nie.txt\nLABEL weiter\n"
                "PRINT Hallo ${name}\nIF name\nDEL ja.txt\nEND\n")
        files, _, said = self.plan(text)
        self.assertEqual(files, ["ja.txt"])
        self.assertEqual(said, ["Hallo Welt"])

    def test_loop_with_question(self):
        text = "TITLE t\nLABEL a\nINPUT nochmal choices=ja,nein\nIF nochmal == ja\nGOTO a\nEND\n"
        asked = iter(["ja", "ja", "nein"])
        patch = parse(text)
        flow.plan(patch, lambda item: next(asked))
        self.assertRaises(StopIteration, next, asked)
        with self.assertRaises(PatchInvalid):          # vorgegebene Antwort -> Endlosschleife
            flow.plan(patch, None, preset={"nochmal": "ja"})

    def test_errors(self):
        for bad in ("TITLE t\nIF x\nDEL a\n", "TITLE t\nEND\n", "TITLE t\nGOTO fehlt\n",
                    "TITLE t\nLABEL a\nDEL x\nGOTO a\n"):
            with self.assertRaises(PatchInvalid):
                flow.plan(parse(bad), lambda item: "")
        with self.assertRaises(flow.FlowExit):
            flow.plan(parse("TITLE t\nEXIT Stop\n"), None)
        with self.assertRaises(UsageError):
            flow.plan(parse("TITLE t\nINPUT x choices=a,b\n"), None, preset={"x": "c"})

    def test_round_trip(self):
        text = ("TITLE t\nINPUT x \"Welche Variante?\" choices=a,b default=a\nIF x == a\n"
                "  DEL a.txt\nELSE\n  PRINT nichts\nEND\n")
        patch = parse(text)
        self.assertEqual(patchfile.dumps_body(patch), text)


class Chat(ProjectCase):
    def setUp(self):
        super().setUp()
        self.write("app.py", APP)
        self.write("style.css", STYLE)
        self.draft = self.write("antwort.txt", DRAFT, root=self.tmp)

    def run_cmd(self, *extra):
        return self.run_cli("run", "-y", self.draft, "-p", self.target, *extra)

    def test_run_default_branch(self):
        self.make_target()
        self.assertEqual(self.run_cmd("-n"), 0, self.last_err)
        self.assertEqual(self.read("app.py"), APP.encode())          # Probelauf
        self.assertEqual(self.run_cmd(), 0, self.last_err)
        self.assertIn(b"float(a)", self.read("app.py"))
        self.assertEqual(self.read("style.css"), STYLE.encode())
        self.assertIn("| Design bleibt.", self.last_out)

    def test_run_with_answer_and_rollback(self):
        self.make_target()
        before = self.snapshot(self.target)
        self.assertEqual(self.run_cmd("--set", "design=ja", "--set", "user=Max"), 0, self.last_err)
        self.assertIn(b"color: white;", self.read("style.css"))
        self.assertIn("| Hinweis fuer Max", self.last_out)
        with open(os.path.join(self.target, ".patcher", "history.json")) as handle:
            entry = json.load(handle)["applied"][-1]
        self.assertEqual(entry["answers"], {"design": "ja"})
        self.assertEqual(self.run_cli("rollback", "-y", "-q", "-p", self.target), 0)
        self.assertEqual(self.snapshot(self.target), before)

    def test_exit_changes_nothing(self):
        self.make_target()
        before = self.snapshot(self.target)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = self.run_cmd("--set", "design=vielleicht")
        self.assertEqual(code, 1)
        self.assertEqual(self.snapshot(self.target), before)

    def test_content_conflict(self):
        self.make_target()
        self.write("app.py", APP.replace("def add", "def plus"), root=self.target)
        self.assertEqual(self.run_cmd(), 3)

    def test_parts_in_separate_files(self):
        one, two = DRAFT.split("Chat-Text zwischen den Teilen.")
        os.remove(self.draft)
        first = self.write("ki.1.ppatch", one, root=self.tmp)
        self.write("ki.2.ppatch", two, root=self.tmp)
        self.make_target()
        self.assertEqual(self.run_cli("run", "-y", first, "-p", self.target), 0, self.last_err)
        os.remove(os.path.join(self.tmp, "ki.2.ppatch"))
        with self.assertRaises(PatchInvalid):
            patchfile.load(first, verify=False)

    def test_seal_flow(self):
        self.write("ki.ppatch", DRAFT.replace("PRINT Hinweis fuer ${user}\n", ""), root=self.tmp)
        out = os.path.join(self.tmp, "sealed.ppatch")
        self.assertEqual(self.run_cli("seal", "-q", os.path.join(self.tmp, "ki.ppatch"),
                                      "--base", self.base, "-o", out), 0, self.last_err)
        patch = patchfile.load(out)
        self.assertIn("hash_before", patch["files"][0])       # in allen Ablaeufen gleich
        self.assertIn("hash_after", patch["files"][1])
        self.make_target()
        self.assertEqual(self.run_cli("apply", "-y", "-q", "--set", "design=ja", out,
                                      "-p", self.target), 0, self.last_err)
        self.assertIn(b"color: white;", self.read("style.css"))


if __name__ == "__main__":
    unittest.main()


class PromptExample(ProjectCase):
    """Das Beispiel aus ki-prompt.txt muss gueltig sein und funktionieren."""

    def test_example(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "ki-prompt.txt"), encoding="utf-8") as handle:
            text = handle.read()
        example = text.split("BEISPIEL\n", 1)[1].split("-----", 1)[0]
        self.write("src/rechner.py", "def add(a, b):\n    return a + b\n")
        draft = self.write("beispiel.ppatch", example, root=self.tmp)
        for answer, expected in (("ja", b"round("), ("nein", b"return float(a) + float(b)")):
            if os.path.exists(self.target):
                shutil.rmtree(self.target)
            self.make_target()
            self.assertEqual(self.run_cli("run", "-y", "--set", "runden=" + answer, draft,
                                          "-p", self.target), 0, self.last_err)
            self.assertIn(expected, self.read("src/rechner.py"))
