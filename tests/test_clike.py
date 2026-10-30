"""mode c / cpp: Normalform, Einheiten, Befehle, create."""

import os
import shutil
import unittest

from helpers import ProjectCase, doc

from pypatcher import adapters, patchfile
from pypatcher import profile as pf
from pypatcher.adapters import ParseError

C = pf.complete({"mode": "c"})

PROG = (
    "#include <stdio.h>\n"
    "\n"
    "/* Punkt */\n"
    "struct point {\n"
    "    int x, y;\n"
    "};\n"
    "\n"
    "static int table[] = {1, 2, 3};\n"
    "\n"
    "int add(int a, int b)\n"
    "{\n"
    "    return a + b;\n"
    "}\n"
    "\n"
    "int main(void) {\n"
    "    printf(\"%d\\n\", add(1, 2));\n"
    "    return 0;\n"
    "}\n"
)

CLASS = (
    "namespace app {\n"
    "\n"
    "class Box {\n"
    "public:\n"
    "    int get() const {\n"
    "        return value;\n"
    "    }\n"
    "private:\n"
    "    int value;\n"
    "};\n"
    "\n"
    "}  // namespace app\n"
)


def h(text, prof=C):
    return adapters.file_hash(doc(text), prof)


class Normalform(unittest.TestCase):
    def test_cosmetics_and_functions(self):
        variant = PROG.replace("/* Punkt */", "// Punkt").replace("int add(int a, int b)\n{",
                                                                  "int add(int a,int b) {")
        self.assertEqual(h(PROG), h(variant))
        self.assertNotEqual(h(PROG), h(PROG.replace("a + b", "a - b")))
        self.assertNotEqual(h(PROG), h(PROG.replace("<stdio.h>", "<stdlib.h>")))
        self.assertNotEqual(h('char *s = "a b";\n'), h('char *s = "a  b";\n'))

    def test_invalid(self):
        with self.assertRaises(ParseError):
            adapters.get("c").validate(doc("int f() {\n"))

    def test_units(self):
        units = dict((u.path, u.kind) for u in adapters.get("c").analyze(doc(PROG)).units)
        self.assertEqual(units, {"point": "struct", "add": "function", "main": "function"})
        cpp = dict((u.path, u.kind) for u in adapters.get("cpp").analyze(doc(CLASS)).units)
        self.assertEqual(cpp, {"app": "namespace", "app::Box": "class", "app::Box::get": "method"})


class CFlow(ProjectCase):
    def setUp(self):
        super().setUp()
        self.write("main.c", PROG)
        self.write("box.hpp", CLASS)

    def test_commands(self):
        patch = self.seal({"id": "0001-c", "title": "c", "files": [
            {"path": "main.c", "action": "modify", "changes": [
                {"op": "replace_unit", "unit": "add",
                 "new": ["int add(int a, int b)", "{", "\treturn b + a;", "}"]},
                {"op": "add_unit", "position": "after:add",
                 "new": ["int sub(int a, int b) {", "\treturn a - b;", "}"]},
                {"op": "insert_text", "scope": "point", "anchor": ["int x, y;"],
                 "position": "after", "new": ["int z;"]}]},
            {"path": "box.hpp", "action": "modify", "changes": [
                {"op": "add_unit", "position": "after:app::Box::get",
                 "new": ["void set(int v) {", "\tvalue = v;", "}"]}]}]})
        self.make_target()
        before = self.snapshot(self.target)
        self.assertEqual(self.apply(patch), 0, self.last_err)
        prog = self.read("main.c").decode()
        self.assertIn("    return b + a;\n}\n\nint sub(int a, int b) {\n    return a - b;\n}\n", prog)
        self.assertIn("    int x, y;\n    int z;\n};\n", prog)
        self.assertIn("    }\n\n    void set(int v) {\n        value = v;\n    }\nprivate:",
                      self.read("box.hpp").decode())
        self.assertEqual(self.run_cli("rollback", "-q", "-y", "-p", self.target), 0)
        self.assertEqual(self.snapshot(self.target), before)

    def test_create(self):
        new = os.path.join(self.tmp, "new")
        shutil.copytree(self.base, new)
        self.write("main.c", PROG.replace("return 0;", "return 1;"), root=new)
        out = os.path.join(self.tmp, "c.ppatch")
        self.assertEqual(self.run_cli("create", "-q", self.base, new, "-o", out, "--id", "c",
                                      "--title", "c"), 0, self.last_err)
        changes = patchfile.load(out)["files"][0]["changes"]
        self.assertEqual([(c["op"], c.get("unit")) for c in changes], [("replace_unit", "main")])
        self.make_target()
        self.assertEqual(self.apply(out), 0, self.last_err)
        self.assertEqual(self.snapshot(self.target), self.snapshot(new))


if __name__ == "__main__":
    unittest.main()
