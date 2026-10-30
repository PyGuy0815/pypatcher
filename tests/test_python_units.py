"""R005: Imports und Zuweisungen als Python-Einheiten."""

from helpers import ProjectCase, doc

from pypatcher.adapters import PY

SOURCE = '''"""Modul."""

# Abhaengigkeiten
import os
from typing import List

# Grenzwert
LIMIT = 10
NAMES: List[str] = ["a",
                    "b"]
a = b = 0


class Config:
    debug = False
    port: int = 8000
    name: str

    def url(self):
        return "http://x:%d" % self.port


def main():
    pass
'''


class PythonUnits(ProjectCase):

    def test_paths(self):
        info = PY.analyze(doc(SOURCE))
        units = dict((u.path, (u.kind, u.start, u.end)) for u in info.units)
        self.assertEqual(units["@imports"], ("imports", 2, 5))
        self.assertEqual(units["LIMIT"], ("assign", 6, 8))
        self.assertEqual(units["NAMES"], ("assign", 8, 10))
        self.assertNotIn("a", units)
        for path in ("Config.debug", "Config.port", "Config.name"):
            self.assertEqual(units[path][0], "assign")
        self.assertEqual(units["Config.url"][0], "method")

    def test_duplicates(self):
        info = PY.analyze(doc("X = 1\nX = 2\n"))
        self.assertEqual([u.path for u in info.units[1:]], ["X#1", "X#2"])

    def apply(self, draft):
        self.write("m.py", SOURCE)
        if not __import__("os").path.isdir(self.target):
            self.make_target()
        path = self.write("d.ppatch", "#PPATCH 1 p 1/1\nTITLE t\nFILE m.py\n" + draft,
                          root=self.tmp)
        self.assertEqual(self.run_cli("run", "-y", path, "-p", self.target), 0, self.last_err)
        return self.read("m.py").decode("utf-8")

    def test_replace_and_add(self):
        text = self.apply("  UNIT LIMIT @1\n  UADD after:LIMIT @2\n  UNIT @imports @3\n"
                          "  UNIT Config.port @4\n  UADD end-of:Config @5\n"
                          "@1 .\nLIMIT = 20\n@.\n@2 .\nTIMEOUT = 5\n@.\n"
                          "@3 .\nimport os\nimport sys\nfrom typing import List\n@.\n"
                          "@4 .\nport: int = 9000\n@.\n@5 .\ndef close(self):\n\tpass\n@.\n")
        # Kommentare direkt ueber einer Einheit gehoeren zu ihr (wie bei def)
        self.assertIn('"""Modul."""\n\nimport os\nimport sys\nfrom typing import List\n\n'
                      'LIMIT = 20\nTIMEOUT = 5\nNAMES', text)
        self.assertIn("    port: int = 9000\n    name: str\n", text)
        self.assertIn("\n\n    def close(self):\n        pass\n", text)

    def test_add_constant_before_function(self):
        text = self.apply("  UADD before:main @1\n@1 .\nVERSION = \"1.0\"\n@.\n")
        self.assertIn('        return "http://x:%d" % self.port\n\n\nVERSION = "1.0"\n\n\n'
                      'def main():', text)

    def test_delete_and_copy_with_rename(self):
        text = self.apply("  UDEL Config.debug\n  UCOPY LIMIT after:LIMIT as=MAXIMUM\n")
        self.assertNotIn("debug = False", text)
        self.assertIn("LIMIT = 10\n# Grenzwert\nMAXIMUM = 10\n", text)
