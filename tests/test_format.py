"""Patchformat pypatcher/1: Befehlsliste, Datenbloecke, Pakete, Pruefstufen."""

import os
import unittest

from helpers import ProjectCase

from pypatcher import patchfile
from pypatcher.errors import PatchInvalid

MODULE = "def add(a, b):\n    return a + b\n\n\nclass K:\n    def run(self):\n        return 1\n"


class Format(ProjectCase):
    def setUp(self):
        super().setUp()
        self.write("m.py", MODULE)
        self.write("notes.txt", "eins\nzwei\n")
        self.draft = {"id": "0001-f", "title": "Format", "description": "Zeile 1\nZeile 2",
                      "files": [
                          {"path": "m.py", "action": "modify", "changes": [
                              {"op": "replace_unit", "unit": "K.run",
                               "new": ["def run(self):", "\treturn 2"]},
                              {"op": "add_unit", "position": "after:add",
                               "new": ["def sub(a, b):", "\treturn a - b"]}]},
                          {"path": "notes.txt", "action": "modify", "changes": [
                              {"op": "replace_text", "old": ["zwei"], "new": ["drei"]}]},
                          {"path": "bild.bin", "action": "add", "new": {"base64": "AAEC"}}]}

    def test_round_trip(self):
        text = patchfile.dumps(self.draft)
        path = os.path.join(self.tmp, "x.ppatch")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)
        patch = patchfile.load(path)
        self.assertEqual(patch["description"], "Zeile 1\nZeile 2")
        self.assertEqual(patch["files"][0]["changes"][0]["new"], ["def run(self):", "\treturn 2"])
        self.assertEqual(patch["files"][2]["new"], {"base64": "AAEC"})
        self.assertEqual(patchfile.dumps(patch), text)

    def test_levels_and_hash_length(self):
        sizes = {}
        self.make_target()
        for level in patchfile.LEVELS:
            path = self.seal(self.draft, "p-%s.ppatch" % level, "--verify", level)
            with open(path, encoding="utf-8") as handle:
                text = handle.read()
            sizes[level] = len(text)
            self.assertEqual(" u=" in text, level != "min")
            self.assertEqual(" c=" in text, level == "full")
            self.assertEqual(self.run_cli("check", path, "-p", self.target), 0, self.last_err)
        self.assertLess(sizes["min"], sizes["std"])
        self.assertLess(sizes["std"], sizes["full"])
        path = self.seal(self.draft, "lang.ppatch", "--hash-len", "64")
        with open(path, encoding="utf-8") as handle:
            self.assertRegex(handle.read(), r"h=[0-9a-f]{64}>[0-9a-f]{64}")

    def test_packages(self):
        self.seal(self.draft, "teil.ppatch", "--max-size", "300")
        parts = sorted(p for p in os.listdir(self.tmp) if p.startswith("teil.") and
                       p.endswith(".ppatch") and p != "teil.ppatch.entwurf")
        self.assertGreater(len(parts), 1)
        for name in parts:
            self.assertLessEqual(os.path.getsize(os.path.join(self.tmp, name)), 300)
        self.make_target()
        last = os.path.join(self.tmp, parts[-1])
        self.assertEqual(self.apply(last), 0, self.last_err)
        self.assertIn(b"return 2", self.read("m.py"))
        missing = os.path.join(self.tmp, parts[0])
        os.remove(missing)
        with self.assertRaises(PatchInvalid):
            patchfile.load(last)

    def test_hashes_are_optional(self):
        path = self.seal(self.draft, "p.ppatch")
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
        stripped = "\n".join(" ".join(t for t in line.split(" ") if not t[:2] in ("h=", "u="))
                             for line in text.splitlines()[1:]) + "\n"
        bare = os.path.join(self.tmp, "ohne.ppatch")
        with open(bare, "w", encoding="utf-8") as handle:
            handle.write(patchfile.split("0001-f", stripped, None)[0])
        self.make_target()
        self.assertEqual(self.apply(bare), 0, self.last_err)

    def test_handwritten_blocks(self):
        text = ("#PPATCH 1 0002-h\nTITLE von Hand\nFILE notes.txt\n"
                "  TEXT @1 @2\n# Kommentar\n@1 .\nzwei\n@.\n@2 .\nzwei\ndrei\n@.\n")
        path = os.path.join(self.tmp, "h.ppatch")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)
        out = os.path.join(self.tmp, "h.sealed.ppatch")
        self.assertEqual(self.run_cli("seal", "-q", path, "--base", self.base, "-o", out), 0,
                         self.last_err)
        self.make_target()
        self.assertEqual(self.apply(out), 0, self.last_err)
        self.assertEqual(self.read("notes.txt"), b"eins\nzwei\ndrei\n")

    def test_draft_needs_seal(self):
        path = os.path.join(self.tmp, "e.ppatch")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("#PPATCH 1 0001-f\nTITLE x\nDEL notes.txt\n")
        with self.assertRaises(PatchInvalid):
            patchfile.load(path)
        self.assertEqual(patchfile.load(path, verify=False)["files"][0]["action"], "delete")


if __name__ == "__main__":
    unittest.main()
