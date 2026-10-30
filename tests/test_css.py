"""mode css / scss: Normalform, Einheiten, Patch-Befehle, create."""

import os
import shutil
import unittest

from helpers import ProjectCase, doc

from pypatcher import adapters, patchfile
from pypatcher import profile as pf
from pypatcher.adapters import ParseError

CSS = pf.complete({"mode": "css"})
SCSS = pf.complete({"mode": "scss"})

STYLE = (
    "@import url(base.css);\n"
    "\n"
    "/* Knopf */\n"
    ".btn, .btn-alt {\n"
    "  color: red;\n"
    "}\n"
    "\n"
    "@media (max-width: 600px) {\n"
    "  .btn {\n"
    "    padding: 0 4px;\n"
    "  }\n"
    "}\n"
)


def h(text, prof=CSS):
    return adapters.file_hash(doc(text), prof)


class Normalform(unittest.TestCase):
    def test_cosmetics_ignored(self):
        variant = STYLE.replace("/* Knopf */", "/* x */").replace("color: red;", "color:red") \
            .replace(".btn, .btn-alt", ".btn,.btn-alt").replace("  .btn {", "\t.btn{")
        self.assertEqual(h(STYLE), h(variant))

    def test_functional_changes(self):
        self.assertNotEqual(h(STYLE), h(STYLE.replace("0 4px", "0 5px")))
        self.assertNotEqual(h("a:hover{x:1}"), h("a :hover{x:1}"))
        self.assertNotEqual(h("a{x:1;y:2}"), h("a{y:2;x:1}"))
        self.assertNotEqual(h("a{content:'a'}"), h("a{content:'a '}"))

    def test_scss(self):
        s = "// k\n$f: red;\n.card {\n  &:hover { color: $f; }\n}\n"
        self.assertEqual(h(s, SCSS), h(s.replace("// k", "// anders"), SCSS))
        with self.assertRaises(ParseError):
            adapters.get("css").validate(doc("a { color: red;\n"))

    def test_units(self):
        info = adapters.get("css").analyze(doc(STYLE))
        paths = [u.path for u in info.units]
        self.assertEqual(paths, [".btn,.btn-alt", "@media (max-width: 600px)",
                                 "@media (max-width: 600px)/.btn"])
        self.assertEqual((info.units[0].start, info.units[0].end), (2, 6))


class CssFlow(ProjectCase):
    def setUp(self):
        super().setUp()
        self.write("style.css", STYLE)

    def test_commands(self):
        patch = self.seal({"id": "0001-css", "title": "css", "files": [
            {"path": "style.css", "action": "modify", "changes": [
                {"op": "replace_unit", "unit": "@media (max-width: 600px)/.btn",
                 "new": [".btn {", "\tpadding: 0;", "}"]},
                {"op": "add_unit", "position": "end-of:@media (max-width: 600px)",
                 "new": [".neu {", "\tdisplay: none;", "}"]},
                {"op": "copy_unit", "from": ".btn,.btn-alt", "as": ".kopie",
                 "position": "after:.btn,.btn-alt"},
                {"op": "replace_text", "scope": ".btn,.btn-alt", "old": ["color: red;"],
                 "new": ["color: blue;"]}]}]})
        with open(patch, encoding="utf-8") as handle:
            self.assertIn('UNIT "@media (max-width: 600px)/.btn"', handle.read())
        self.make_target()
        before = self.snapshot(self.target)
        self.assertEqual(self.apply(patch), 0, self.last_err)
        result = self.read("style.css").decode()
        self.assertIn("  .btn {\n    padding: 0;\n  }\n  .neu {\n    display: none;\n  }\n}\n",
                      result)
        self.assertIn("/* Knopf */\n.kopie {\n  color: red;\n}\n", result)
        self.assertIn(".btn, .btn-alt {\n  color: blue;\n}\n", result)
        self.assertEqual(self.run_cli("rollback", "-q", "-y", "-p", self.target), 0)
        self.assertEqual(self.snapshot(self.target), before)

    def test_create(self):
        new = os.path.join(self.tmp, "new")
        shutil.copytree(self.base, new)
        self.write("style.css", STYLE.replace("0 4px", "0 8px") + "\n.extra {\n  top: 0;\n}\n",
                   root=new)
        out = os.path.join(self.tmp, "c.ppatch")
        self.assertEqual(self.run_cli("create", "-q", self.base, new, "-o", out, "--id", "c",
                                      "--title", "c"), 0, self.last_err)
        ops = [ch["op"] for ch in patchfile.load(out)["files"][0]["changes"]]
        self.assertEqual(sorted(ops), ["add_unit", "replace_unit"])
        self.make_target()
        self.assertEqual(self.apply(out), 0, self.last_err)
        self.assertEqual(self.snapshot(self.target), self.snapshot(new))


if __name__ == "__main__":
    unittest.main()
