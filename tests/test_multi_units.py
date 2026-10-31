"""Mehrere Einheiten in einem UADD/UNIT-Block (Praxistest: drei neue
Funktionen in einem Block) und weitere Praxistest-Punkte."""

import json
import os

from helpers import ProjectCase

APP = '''import sys


def cmd_info(args):
    return 0


def build_parser():
    return None
'''

THREE = ("@1 .\ndef cmd_a(args):\n\treturn 1\n\n\ndef cmd_b(args):\n\treturn 2\n\n\n"
         "def cmd_c(args):\n\treturn 3\n@.\n")


class MultiUnits(ProjectCase):

    def setUp(self):
        super().setUp()
        self.write("app.py", APP)
        self.make_target()

    def draft(self, body, name="d.ppatch"):
        return self.write(name, "#PPATCH 1 m 1/1\nTITLE t\nFILE app.py\n" + body, root=self.tmp)

    def test_uadd_three_functions(self):
        self.assertEqual(self.run_cli("run", "-y", self.draft("  UADD before:build_parser @1\n"
                                                              + THREE), "-p", self.target),
                         0, self.last_err)
        self.assertIn("Block enthaelt 3 Einheiten: cmd_a, cmd_b, cmd_c", self.last_out)
        text = self.read("app.py").decode()
        self.assertIn("    return 0\n\n\ndef cmd_a(args):\n    return 1\n\n\ndef cmd_b", text)
        self.assertIn("    return 3\n\n\ndef build_parser():", text)

    def test_sealed_multi_unit_patch(self):
        sealed = os.path.join(self.tmp, "s.ppatch")
        self.assertEqual(self.run_cli("seal", self.draft("  UADD before:build_parser @1\n"
                                                         + THREE), "--base", self.base,
                                      "-o", sealed), 0, self.last_err)
        self.assertEqual(self.run_cli("apply", "-y", "-q", sealed, "-p", self.target), 0,
                         self.last_err)
        self.assertEqual(self.run_cli("rollback", "-y", "-q", "-p", self.target), 0)
        self.assertEqual(self.read("app.py"), APP.encode())

    def test_unit_replaced_by_function_and_helper(self):
        draft = self.draft("  UNIT cmd_info @1\n@1 .\ndef _helper():\n\treturn 0\n\n\n"
                           "def cmd_info(args):\n\treturn _helper()\n@.\n")
        self.assertEqual(self.run_cli("run", "-y", draft, "-p", self.target), 0, self.last_err)
        self.assertIn("def _helper():\n    return 0\n\n\ndef cmd_info(args):",
                      self.read("app.py").decode())

    def test_loose_code_still_rejected(self):
        draft = self.draft("  UADD end @1\n@1 .\ndef cmd_x():\n\tpass\n\n\nprint(1)\n@.\n")
        self.assertEqual(self.run_cli("run", "-n", draft, "-p", self.target), 2)
        self.assertIn("nicht aus ganzen Einheiten", self.last_err)


class JsMultiMembers(ProjectCase):

    def test_two_object_methods_with_commas(self):
        self.write("a.js", "const api = {\n  load() {\n    return 1;\n  }\n};\n")
        self.make_target()
        draft = self.write("d.ppatch", "#PPATCH 1 j 1/1\nTITLE t\nFILE a.js\n"
                           "  UADD end-of:api @1\n@1 .\nsave() {\n  return 2;\n}\n\n"
                           "drop() {\n  return 3;\n}\n@.\n", root=self.tmp)
        self.assertEqual(self.run_cli("run", "-y", draft, "-p", self.target), 0, self.last_err)
        self.assertEqual(self.read("a.js").decode(),
                         "const api = {\n  load() {\n    return 1;\n  },\n\n  save() {\n"
                         "    return 2;\n  },\n\n  drop() {\n    return 3;\n  }\n};\n")


class FieldTestMessages(ProjectCase):

    def test_unit_without_file(self):
        self.write("app.py", APP)
        self.make_target()
        draft = self.write("d.ppatch", "#PPATCH 1 f 1/1\nTITLE t\nUNIT cmd_info @1\n"
                           "@1 .\ndef cmd_info(args):\n\treturn 1\n@.\n", root=self.tmp)
        self.assertEqual(self.run_cli("run", "-n", draft, "-p", self.target), 2)
        self.assertIn("'UNIT cmd_info @1'", self.last_err)
        self.assertIn("'FILE <pfad>' schreiben", self.last_err)

    def test_rollback_on_failure_from_config(self):
        self.write("app.py", "x = 1\n")
        self.make_target()
        config = os.path.join(self.target, ".patcher", "config.json")
        with open(config, "w", encoding="utf-8") as handle:
            json.dump({"test": "python3 -c \"import app; assert app.x == 2\"",
                       "rollback_on_failure": True}, handle)
        draft = self.write("d.ppatch", "#PPATCH 1 r 1/1\nTITLE t\nREP app.py @1\n"
                           "@1 .\nx = 3\n@.\n", root=self.tmp)
        self.assertEqual(self.run_cli("run", "-y", "-q", draft, "-p", self.target), 6)
        self.assertIn("zurueckgenommen", self.last_out)
        self.assertEqual(self.read("app.py"), b"x = 1\n")
