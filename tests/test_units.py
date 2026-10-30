"""Einheiten (manifest.txt 7.3) und relative Einrueckung (6.1)."""

import unittest

from helpers import doc

from pypatcher import layout as lay
from pypatcher.adapters import PY
from pypatcher.errors import Conflict

SOURCE = (
    "#!/usr/bin/env python3\n"     # 1
    "# Kopf\n"                     # 2
    "def direkt():\n"              # 3  folgt ohne Leerzeile auf den Kopf
    "    pass\n"                   # 4
    "\n"                           # 5
    "\n"                           # 6
    "# gehoert zu f\n"             # 7
    "@deco\n"                      # 8
    "def f():\n"                   # 9
    "    pass\n"                   # 10
    "\n"                           # 11
    "\n"                           # 12
    "class K:\n"                   # 13
    "    def g(self):\n"           # 14
    "        pass\n"               # 15
    "\n"                           # 16
    "    @property\n"              # 17
    "    def g(self):\n"           # 18
    "        pass\n"               # 19
)


class Units(unittest.TestCase):
    def setUp(self):
        self.info = PY.analyze(doc(SOURCE))
        self.units = dict((u.path, u) for u in self.info.units)

    def test_header_has_priority(self):
        self.assertEqual(self.units["@header"].end, 2)
        self.assertEqual(self.units["direkt"].start, 2)

    def test_comments_and_decorators_belong_to_unit(self):
        unit = self.units["f"]
        self.assertEqual((unit.start, unit.end), (6, 10))

    def test_duplicates_need_index(self):
        self.assertIn("K.g#1", self.units)
        self.assertIn("K.g#2", self.units)
        with self.assertRaises(Conflict):
            PY.find(self.info, "K.g")

    def test_kinds_and_indent(self):
        self.assertEqual(self.units["K"].kind, "class")
        self.assertEqual(self.units["K.g#2"].kind, "method")
        self.assertEqual(self.units["K.g#2"].indent, "    ")
        self.assertEqual(PY.detect_unit(doc(SOURCE)), "    ")

    def test_header_with_docstring(self):
        info = PY.analyze(doc('# a\n\n"""Doku."""\n\nimport os\n'))
        self.assertEqual(info.units[0].end, 3)
        idx, base, top = PY.position(info, "start")
        self.assertEqual((idx, base, top), (5, "", True))


class Layout(unittest.TestCase):
    def test_round_trip_with_alignment(self):
        lines = ["    def f(self, a,", "              b):", "        return a"]
        rel = lay.to_rel(lines, "    ", "    ")
        self.assertEqual(rel, ["def f(self, a,", "\t\t  b):", "\treturn a"])
        self.assertEqual(lay.to_abs(rel, "    ", "    "), lines)
        self.assertEqual(lay.to_abs(rel, "\t", "\t"),
                         ["\tdef f(self, a,", "\t\t\t  b):", "\t\treturn a"])

    def test_not_representable(self):
        self.assertIsNone(lay.to_rel(["    x", "\ty"], "", "    "))
        self.assertIsNone(lay.to_rel(["  x"], "    ", "    "))

    def test_layout_parameter(self):
        self.assertEqual(lay.unit_from_layout({"indent": "spaces", "width": 2}), "  ")
        self.assertEqual(lay.unit_from_layout({"indent": "tabs"}), "\t")
        self.assertEqual(lay.layout_for("    "), {"indent": "spaces", "width": 4})


if __name__ == "__main__":
    unittest.main()
