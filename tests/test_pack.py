"""Komprimierung (z64), USES, pack und Einzeldatei (bundle)."""

import os
import subprocess
import sys
import unittest

from helpers import ProjectCase

from pypatcher import patchfile
from pypatcher.errors import PatchInvalid

BODY = "".join("def f%d(x):\n    return x * %d  # Zeile mit etwas Text\n\n\n" % (i, i)
               for i in range(60))


class Pack(ProjectCase):
    def setUp(self):
        super().setUp()
        self.write("m.py", BODY)
        self.draft = {"id": "0001-z", "title": "z", "files": [
            {"path": "m.py", "action": "modify", "changes": [
                {"op": "replace_unit", "unit": "f%d" % i,
                 "new": ["def f%d(x):" % i, "\treturn x + %d  # neu, mit etwas Text" % i]}
                for i in range(40)]}]}

    def size(self, path):
        with open(path, encoding="utf-8") as handle:
            return len(handle.read())

    def test_compressed_patch(self):
        plain = self.seal(self.draft, "plain.ppatch")
        packed = self.seal(self.draft, "packed.ppatch", "--compress")
        self.assertLess(self.size(packed), self.size(plain) * 0.7)
        with open(packed, encoding="utf-8") as handle:
            text = handle.read()
        self.assertIn("USES z64", text)
        self.assertIn("  UNIT f0 ", text)                    # Befehlsliste bleibt lesbar
        patch = patchfile.load(packed)
        self.assertTrue(patch["compressed"])
        self.make_target()
        self.assertEqual(self.apply(packed), 0, self.last_err)
        self.assertIn(b"return x + 39", self.read("m.py"))

    def test_pack_keeps_hashes_and_splits(self):
        sealed = self.seal(self.draft, "s.ppatch")
        out = os.path.join(self.tmp, "p.ppatch")
        self.assertEqual(self.run_cli("pack", "-q", sealed, "-o", out, "--compress",
                                      "--max-size", "1500"), 0, self.last_err)
        parts = [p for p in os.listdir(self.tmp) if p.startswith("p.") and p != "p.ppatch"]
        self.assertGreater(len(parts), 1)
        original, packed = patchfile.load(sealed), patchfile.load(os.path.join(self.tmp, parts[0]))
        self.assertEqual(original["files"][0]["hash_before"], packed["files"][0]["hash_before"])
        self.make_target()
        self.assertEqual(self.apply(os.path.join(self.tmp, parts[-1])), 0, self.last_err)

    def test_pack_draft(self):
        draft = self.write_draft(self.draft, "e.ppatch")
        with open(draft, "w", encoding="utf-8") as handle:     # Entwurf ohne Hash
            handle.write("#PPATCH 1 0001-z\n" + patchfile.dumps_body(self.draft))
        out = os.path.join(self.tmp, "ez.ppatch")
        self.assertEqual(self.run_cli("pack", "-q", draft, "-o", out, "--compress"), 0)
        with open(out, encoding="utf-8") as handle:
            self.assertTrue(handle.readline().strip() == "#PPATCH 1 0001-z")
        self.make_target()
        self.assertEqual(self.run_cli("run", "-y", "-q", out, "-p", self.target), 0, self.last_err)

    def test_unknown_features_rejected(self):
        for text, word in (("TITLE t\nUSES flow zukunft\nDEL a\n", "zukunft"),
                           ("TITLE t\nADD a @1\n@1 1 zz9\nx\n", "zz9")):
            with self.assertRaises(PatchInvalid) as ctx:
                patchfile.parse_body(text)
            self.assertIn(word, str(ctx.exception))
            self.assertIn("neuere", str(ctx.exception))

    def test_damaged_compression(self):
        text = "TITLE t\nADD a @1\n@0 1 z64\nAAAA\n"
        with self.assertRaises(PatchInvalid):
            patchfile.parse_body(text)

    def test_bundle(self):
        pyz = os.path.join(self.tmp, "patcher.pyz")
        self.assertEqual(self.run_cli("bundle", "-q", "-o", pyz), 0, self.last_err)
        result = subprocess.run([sys.executable, pyz, "--version"], stdout=subprocess.PIPE,
                                universal_newlines=True)
        self.assertEqual(result.returncode, 0)
        self.assertIn("pypatcher", result.stdout)


if __name__ == "__main__":
    unittest.main()
