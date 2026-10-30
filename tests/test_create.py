"""create: erzeugter Patch muss exakt zum neuen Stand fuehren."""

import os
import shutil
import unittest

from helpers import ProjectCase

from pypatcher import patchfile

CALC = (
    "#!/usr/bin/env python3\n"
    '"""Modul."""\n'
    "\n"
    "import os\n"
    "\n"
    "\n"
    "# addiert\n"
    "def add(a, b):\n"
    "    return a + b\n"
    "\n"
    "\n"
    "def helper():\n"
    "    return 42\n"
    "\n"
    "\n"
    "class Calc:\n"
    '    """Rechner."""\n'
    "\n"
    "    def run(self, x):\n"
    "        if x:\n"
    "            return 1\n"
    "        return 0\n"
    "\n"
    "    def stop(self):\n"
    "        pass\n"
)


class Create(ProjectCase):
    def setUp(self):
        super().setUp()
        self.new = os.path.join(self.tmp, "new")
        self.write("src/calc.py", CALC)
        self.write("src/other.py", "def other():\n    return 1\n")
        self.write("docs/readme.txt", "Titel\n\nText eins.\nText zwei.\n")
        self.write("old.txt", "weg\n")
        self.write("logo.bin", b"\x00\x01", binary=True)

    def new_tree(self):
        shutil.copytree(self.base, self.new)

    def create(self, *extra):
        out = os.path.join(self.tmp, "c.ppatch")
        code = self.run_cli("create", self.base, self.new, "-o", out,
                            "--id", "0001-create", "--title", "Test", *extra)
        self.assertEqual(code, 0, self.last_err)
        return out, patchfile.load(out)

    def apply_and_compare(self, patch):
        self.make_target()
        self.assertEqual(self.apply(patch), 0, self.last_err)
        self.assertEqual(self.snapshot(self.target), self.snapshot(self.new))
        self.assertEqual(self.run_cli("verify", "-q", "-p", self.target), 0, self.last_out)
        self.assertEqual(self.run_cli("rollback", "-q", "-y", "-p", self.target), 0)
        self.assertEqual(self.snapshot(self.target), self.snapshot(self.base))

    def ops(self, patch, path):
        entry = next(e for e in patch["files"] if e["path"] == path)
        return [ch["op"] for ch in entry.get("changes", [])], entry

    def test_units_text_and_files(self):
        self.new_tree()
        calc = (CALC.replace("            return 1", "            return 2")
                .replace("import os\n", "import os\nimport sys\n")
                .replace("def helper():\n    return 42\n\n\n", "")
                + "\n    def reset(self):\n        self.x = 0\n")
        self.write("src/calc.py", calc, root=self.new)
        self.write("docs/readme.txt", "Titel\n\nText eins.\nText zwei, neu.\n", root=self.new)
        os.remove(os.path.join(self.new, "old.txt"))
        self.write("src/neu.py", "def neu():\n    pass\n", root=self.new)
        os.rename(os.path.join(self.new, "src", "other.py"), os.path.join(self.new, "lib.py"))
        self.write("logo.bin", b"\x00\x02", binary=True, root=self.new)
        path, patch = self.create()
        ops, entry = self.ops(patch, "src/calc.py")
        self.assertIn("replace_unit", ops)
        self.assertIn("delete_unit", ops)
        self.assertIn("add_unit", ops)
        replaced = [ch["unit"] for ch in entry["changes"] if ch["op"] == "replace_unit"]
        self.assertEqual(replaced, ["@imports", "Calc.run"])
        actions = dict((e["path"], e["action"]) for e in patch["files"])
        self.assertEqual(actions["src/other.py"], "move")
        self.assertEqual(actions["old.txt"], "delete")
        self.assertEqual(actions["src/neu.py"], "add")
        self.assertEqual(actions["logo.bin"], "replace")
        self.assertEqual(actions["docs/readme.txt"], "modify")
        self.apply_and_compare(path)

    def test_cross_file_move_and_copy(self):
        self.new_tree()
        calc = CALC.replace("def helper():\n    return 42\n\n\n", "")
        self.write("src/calc.py", calc, root=self.new)
        self.write("src/other.py", "def other():\n    return 1\n\n\ndef helper():\n    return 42\n\n\n"
                   "# addiert\ndef add(a, b):\n    return a + b\n", root=self.new)
        path, patch = self.create()
        ops_other, _ = self.ops(patch, "src/other.py")
        ops_calc, _ = self.ops(patch, "src/calc.py")
        self.assertIn("move_unit", ops_other)
        self.assertIn("move_unit", ops_calc)
        self.assertIn("copy_unit", ops_other)
        order = [e["path"] for e in patch["files"]]
        self.assertLess(order.index("src/other.py"), order.index("src/calc.py"))
        self.apply_and_compare(path)

    def test_reordered_units_fall_back_to_text(self):
        self.new_tree()
        calc = CALC.replace("# addiert\ndef add(a, b):\n    return a + b\n\n\n", "") \
            .replace("class Calc:", "# addiert\ndef add(a, b):\n    return a + b\n\n\nclass Calc:")
        self.write("src/calc.py", calc, root=self.new)
        path, _ = self.create()
        self.apply_and_compare(path)

    def test_cosmetic_only(self):
        self.new_tree()
        self.write("src/other.py", "def other():\n    return 1  # Kommentar\n", root=self.new)
        self.write("docs/readme.txt", "Titel\n\nText eins.\nText zwei.\n", root=self.new)
        self.write("x.txt", "x\n", root=self.new)
        _, patch = self.create()
        self.assertNotIn("src/other.py", [e["path"] for e in patch["files"]])
        self.assertIn("nur kosmetisch", self.last_out)
        path, patch = self.create("--include-cosmetic")
        self.assertIn("src/other.py", [e["path"] for e in patch["files"]])
        self.apply_and_compare(path)

    def test_crlf_and_levels(self):
        self.write("src/win.py", CALC.replace("\n", "\r\n"))
        self.new_tree()
        self.write("src/win.py", CALC.replace("return 0", "return -1").replace("\n", "\r\n"),
                   root=self.new)
        path, patch = self.create("--level", "text")
        ops, _ = self.ops(patch, "src/win.py")
        self.assertEqual(ops, ["replace_text"])
        self.apply_and_compare(path)
        path, patch = self.create("--level", "file")
        self.assertEqual(self.ops(patch, "src/win.py")[1]["action"], "replace")

    def test_no_differences(self):
        self.new_tree()
        out = os.path.join(self.tmp, "c.ppatch")
        self.assertEqual(self.run_cli("create", self.base, self.new, "-o", out,
                                      "--id", "x", "--title", "t"), 2)


if __name__ == "__main__":
    unittest.main()
