"""R038: --json - ein JSON-Objekt je Aufruf (Format pypatcher-result/1)."""

import json

from helpers import ProjectCase

APP = "class Store:\n    def find(self, word):\n        return word\n"


class JsonOutput(ProjectCase):

    def setUp(self):
        super().setUp()
        self.write("app.py", APP)
        self.make_target()

    def call(self, *argv):
        code = self.run_cli(*argv)
        self.assertEqual(self.last_out.count("\n"), 1, self.last_out)
        data = json.loads(self.last_out)
        self.assertEqual(data["format"], "pypatcher-result/1")
        self.assertEqual(data["exit_code"], code)
        self.assertEqual(data["ok"], code == 0)
        return code, data

    def draft(self, body):
        return self.write("d.ppatch", "#PPATCH 1 j 1/1\nTITLE Test\nFILE app.py\n" + body,
                          root=self.tmp)

    def test_run_ok_with_tests(self):
        draft = self.draft("  TEXT @1 @2\n@1 .\nreturn word\n@.\n@2 .\nreturn word.lower()\n@.\n")
        code, data = self.call("run", "--json", "-y", "--test", "true", draft, "-p", self.target)
        self.assertEqual(code, 0)
        self.assertEqual(data["result"], "OK")
        self.assertTrue(data["applied"])
        self.assertEqual(data["patch"], {"id": "j", "title": "Test", "sealed": False})
        self.assertEqual(data["files"], [{"path": "app.py", "change": "modify"}])
        self.assertTrue(data["journal"]["ok"])
        self.assertTrue(data["tests"]["ok"])

    def test_block_not_found_detail(self):
        draft = self.draft("  TEXT @1 @2\n@1 .\nreturn wort\n@.\n@2 .\nreturn 1\n@.\n")
        code, data = self.call("run", "--json", "-n", draft, "-p", self.target)
        self.assertEqual((code, data["result"]), (3, "CONFLICT"))
        error = data["error"]
        self.assertEqual(error["path"], "app.py")
        self.assertEqual(error["change"], {"index": 1, "op": "replace_text"})
        self.assertEqual(error["detail"]["kind"], "block_not_found")
        self.assertEqual(error["detail"]["closest"]["line_from"], 3)
        self.assertEqual(error["detail"]["closest"]["file_lines"], ["        return word"])

    def test_unit_not_found_detail(self):
        draft = self.draft("  UNIT Store.fnd @1\n@1 .\ndef find(self, word):\n\treturn 1\n@.\n")
        code, data = self.call("run", "--json", "-n", draft, "-p", self.target)
        detail = data["error"]["detail"]
        self.assertEqual(detail["kind"], "unit_not_found")
        self.assertEqual(detail["similar"][0], "Store.find")

    def test_json_needs_yes(self):
        draft = self.draft("  TEXT @1 @2\n@1 .\nreturn word\n@.\n@2 .\nreturn 1\n@.\n")
        code, data = self.call("apply", "--json", draft, "-p", self.target)
        self.assertEqual(data["result"], "PATCH_INVALID")
        code, data = self.call("run", "--json", draft, "-p", self.target)
        self.assertEqual((code, data["error"]["code"]), (1, "USAGE"))

    def test_tests_failed_and_rollback(self):
        draft = self.draft("  TEXT @1 @2\n@1 .\nreturn word\n@.\n@2 .\nreturn 1\n@.\n")
        code, data = self.call("run", "--json", "-y", "--test", "echo kaputt; false",
                               "--rollback-on-failure", draft, "-p", self.target)
        self.assertEqual((code, data["result"]), (6, "TESTS_FAILED"))
        self.assertTrue(data["rolled_back"])
        self.assertIn("kaputt", data["tests"]["output"])

    def test_status_verify_log(self):
        self.write("neu.txt", "x", root=self.target)
        code, data = self.call("verify", "--json", "-p", self.target)
        self.assertEqual(data["journal"]["unmanaged"], ["neu.txt"])
        code, data = self.call("status", "--json", "-p", self.target)
        self.assertEqual(data["status"]["managed"], 1)
        code, data = self.call("log", "--json", "-p", self.target)
        self.assertEqual(data["history"]["applied"], [])

    def test_questions_use_defaults(self):
        draft = self.write("q.ppatch", "#PPATCH 1 q 1/1\nTITLE t\nINPUT farbe \"Farbe?\" "
                           "choices=rot,blau default=blau\nPRINT Hallo\nFILE app.py\n"
                           "  TEXT @1 @2\n@1 .\nreturn word\n@.\n@2 .\nreturn 1\n@.\n",
                           root=self.tmp)
        code, data = self.call("run", "--json", "-n", draft, "-p", self.target)
        self.assertEqual(data["answers"], {"farbe": "blau"})
        self.assertEqual(data["print"], ["Hallo"])
