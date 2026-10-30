"""mode js: Normalform, Einheiten, Patch-Befehle, create."""

import os
import shutil
import unittest

from helpers import ProjectCase, doc

from pypatcher import adapters, patchfile
from pypatcher import profile as pf
from pypatcher.adapters import ParseError

JS = pf.complete({"mode": "js"})

APP = (
    "// App\n"
    "import { log } from \"./log.js\";\n"
    "\n"
    "/**\n"
    " * Addiert.\n"
    " */\n"
    "export function add(a, b) {\n"
    "  return a + b;\n"
    "}\n"
    "\n"
    "const mul = (a, b) => {\n"
    "  return a * b;\n"
    "};\n"
    "\n"
    "export class Rechner {\n"
    "  constructor(x) {\n"
    "    this.x = x;\n"
    "  }\n"
    "\n"
    "  run(y) {\n"
    "    if (y) {\n"
    "      return add(this.x, y);\n"
    "    }\n"
    "    return 0;\n"
    "  }\n"
    "}\n"
)


def h(text):
    return adapters.file_hash(doc(text), JS)


class Normalform(unittest.TestCase):
    def test_cosmetics_ignored(self):
        variant = APP.replace("// App", "// anders").replace(" * Addiert.", " * Summe.") \
            .replace("  return a + b;", "\treturn a+b ;").replace("(a, b) =>", "(a,b)=>")
        self.assertEqual(h(APP), h(variant))

    def test_functional_changes(self):
        self.assertNotEqual(h(APP), h(APP.replace("a * b", "a / b")))
        self.assertNotEqual(h("let s = 'a';\n"), h('let s = "a ";\n'))
        self.assertNotEqual(h("let t = `x${a}`;\n"), h("let t = `x${ a }`;\n"))

    def test_asi_relevant_line_breaks(self):
        self.assertNotEqual(h("function f() { return\nx; }\n"), h("function f() { return x; }\n"))
        self.assertNotEqual(h("a = b\n(c)\n"), h("a = b(c)\n"))
        self.assertEqual(h("f(a,\n  b);\n"), h("f(a, b);\n"))

    def test_regex_and_division(self):
        self.assertNotEqual(h("x = a / b / c;\n"), h("x = a / b // c;\n"))
        self.assertEqual(h("r = /a\\/b[/]/g;\n"), h("r = /a\\/b[/]/g; // x\n"))

    def test_invalid(self):
        for bad in ("let s = 'offen;\n", "function f() {\n", "x = `a${b`;\n"):
            with self.assertRaises(ParseError):
                adapters.get("js").validate(doc(bad))


class Units(unittest.TestCase):
    def test_units(self):
        info = adapters.get("js").analyze(doc(APP))
        units = dict((u.path, (u.kind, u.start, u.end)) for u in info.units)
        self.assertEqual(units["add"], ("function", 3, 9))          # inkl. JSDoc
        self.assertEqual(units["mul"], ("function", 10, 13))
        self.assertEqual(units["Rechner.run"], ("method", 19, 25))
        self.assertEqual(info.start_line, 2)


class JsFlow(ProjectCase):
    def setUp(self):
        super().setUp()
        self.write("app.js", APP)
        self.write("util.js", "export function noop() {\n  return null;\n}\n")

    def flow(self, changes, extra_files=()):
        files = [{"path": "app.js", "action": "modify", "changes": changes}] + list(extra_files)
        patch = self.seal({"id": "0001-js", "title": "js", "files": files})
        self.make_target()
        before = self.snapshot(self.target)
        self.assertEqual(self.apply(patch), 0, self.last_err)
        result = self.read("app.js").decode()
        self.assertEqual(self.run_cli("rollback", "-q", "-y", "-p", self.target), 0)
        self.assertEqual(self.snapshot(self.target), before)
        return result

    def test_unit_commands(self):
        result = self.flow([
            {"op": "replace_unit", "unit": "Rechner.run",
             "new": ["run(y) {", "\treturn y ? add(this.x, y) : 0;", "}"]},
            {"op": "add_unit", "position": "after:mul",
             "new": ["function sub(a, b) {", "\treturn a - b;", "}"]},
            {"op": "add_unit", "position": "end-of:Rechner",
             "new": ["reset() {", "\tthis.x = 0;", "}"]},
            {"op": "copy_unit", "from": "add", "as": "plus", "position": "end"},
            {"op": "replace_text", "scope": "add", "old": ["return a + b;"],
             "new": ["return Number(a) + Number(b);"]}])
        self.assertIn("  run(y) {\n    return y ? add(this.x, y) : 0;\n  }\n", result)
        self.assertIn("};\n\nfunction sub(a, b) {\n  return a - b;\n}\n\nexport class", result)
        self.assertIn("  }\n\n  reset() {\n    this.x = 0;\n  }\n}\n", result)
        self.assertIn("export function add(a, b) {\n  return Number(a) + Number(b);\n}\n", result)
        # Kopie vor der Textaenderung an add -> alter Rumpf, inkl. JSDoc
        self.assertTrue(result.endswith(" */\nexport function plus(a, b) {\n  return a + b;\n}\n"))

    def test_move_between_files(self):
        patch = self.seal({"id": "0002-js", "title": "move", "files": [
            {"path": "util.js", "action": "modify", "changes": [
                {"op": "move_unit", "from": "app.js::mul", "position": "end", "move_id": "m"}]},
            {"path": "app.js", "action": "modify", "changes": [
                {"op": "move_unit", "unit": "mul", "to": "util.js", "move_id": "m"}]}]})
        self.make_target()
        self.assertEqual(self.apply(patch), 0, self.last_err)
        self.assertNotIn("const mul", self.read("app.js").decode())
        self.assertTrue(self.read("util.js").decode().endswith(
            "}\n\nconst mul = (a, b) => {\n  return a * b;\n};\n"))

    def test_conflict_on_functional_change(self):
        patch = self.seal({"id": "0003-js", "title": "x", "files": [
            {"path": "app.js", "action": "modify", "changes": [
                {"op": "replace_unit", "unit": "add",
                 "new": ["export function add(a, b) {", "\treturn b + a;", "}"]}]}]})
        self.make_target()
        self.write("app.js", APP.replace("a * b", "a ** b"), root=self.target)
        self.assertEqual(self.apply(patch), 3)
        self.write("app.js", APP.replace("// App", "// geaendert"), root=self.target)
        self.assertEqual(self.apply(patch), 0, self.last_err)

    def test_create(self):
        new = os.path.join(self.tmp, "new")
        shutil.copytree(self.base, new)
        self.write("app.js", APP.replace("this.x = x;", "this.x = x || 0;")
                   .replace("export class", "function neu() {\n  return 1;\n}\n\nexport class"),
                   root=new)
        out = os.path.join(self.tmp, "c.ppatch")
        self.assertEqual(self.run_cli("create", "-q", self.base, new, "-o", out, "--id", "c",
                                      "--title", "c"), 0, self.last_err)
        patch = patchfile.load(out)
        ops = [ch["op"] for ch in patch["files"][0]["changes"]]
        self.assertIn("replace_unit", ops)
        self.assertIn("add_unit", ops)
        self.make_target()
        self.assertEqual(self.apply(out), 0, self.last_err)
        self.assertEqual(self.snapshot(self.target), self.snapshot(new))


if __name__ == "__main__":
    unittest.main()
