"""R024 (JSONC) und R020 (--answers)."""

import json

from helpers import ProjectCase, doc

from pypatcher import adapters
from pypatcher.adapters.base import ParseError

JSONC = adapters.get("jsonc")
PROF = {"keyorder": "ignore", "whitespace": "ignore"}

TSCONFIG = '''{
  // Compiler
  "compilerOptions": {
    "target": "ES2020", // Ziel
    /* streng */
    "strict": true,
    "paths": {
      "@/*": ["src/*"],
    },
  },
  "include": ["src"],
}
'''


class Jsonc(ProjectCase):

    def test_units_and_comments(self):
        info = JSONC.analyze(doc(TSCONFIG))
        units = dict((u.path, (u.start, u.end)) for u in info.units)
        self.assertEqual(units["compilerOptions"], (1, 10))
        self.assertEqual(units["compilerOptions/strict"], (4, 6))
        self.assertIn("compilerOptions/paths/@~1*", units)

    def test_normal_form(self):
        other = TSCONFIG.replace("// Ziel", "").replace('["src/*"],', '["src/*"]')
        self.assertEqual(JSONC.normalize(doc(TSCONFIG), PROF), JSONC.normalize(doc(other), PROF))
        self.assertEqual(JSONC.normalize(doc(TSCONFIG), PROF), adapters.get("json").normalize(
            doc('{"include": ["src"], "compilerOptions": {"strict": true, "target": "ES2020",'
                ' "paths": {"@/*": ["src/*"]}}}'), PROF))
        with self.assertRaises(ParseError):
            JSONC.validate(doc('{"a": 1 /* offen\n}'))

    def test_patch(self):
        self.write("tsconfig.json", TSCONFIG)
        self.make_target()
        draft = self.write("d.ppatch", "#PPATCH 1 c 1/1\nTITLE t\nFILE tsconfig.json\n"
                           "  UNIT compilerOptions/target @1\n  UADD after:compilerOptions/strict @2\n"
                           '@1 .\n"target": "ES2022" // neu\n@.\n'
                           '@2 .\n"noEmit": true\n@.\n', root=self.tmp)
        self.assertEqual(self.run_cli("run", "-y", draft, "-p", self.target), 0, self.last_err)
        text = self.read("tsconfig.json").decode()
        self.assertIn('    "target": "ES2022", // neu\n', text)
        self.assertIn('    "strict": true,\n    "noEmit": true,\n    "paths"', text)


class Answers(ProjectCase):

    DRAFT = ("#PPATCH 1 a 1/1\nTITLE t\nINPUT farbe \"Farbe?\" choices=rot,blau\n"
             "INPUT name \"Name?\"\nFILE a.txt\n  TEXT @1 @2\n"
             "@1 .\nx\n@.\n@2 .\n${farbe}\n@.\n")

    def setUp(self):
        super().setUp()
        self.write("a.txt", "x\n")
        self.make_target()

    def test_answers_file(self):
        draft = self.write("d.ppatch", self.DRAFT.replace("${farbe}", "neu"), root=self.tmp)
        answers = self.write("antworten.txt", "# fuer Test\nfarbe = blau\nname=Ada\n", root=self.tmp)
        self.assertEqual(self.run_cli("run", "-y", "--answers", answers, draft,
                                      "-p", self.target), 0, self.last_err)
        self.assertIn("farbe=blau", self.last_out)

    def test_json_and_set_override(self):
        draft = self.write("d.ppatch", self.DRAFT.replace("${farbe}", "neu"), root=self.tmp)
        answers = self.write("a.json", json.dumps({"farbe": "rot", "name": "Ada"}), root=self.tmp)
        self.assertEqual(self.run_cli("run", "-n", "--answers", answers, "--set", "farbe=blau",
                                      draft, "-p", self.target), 0, self.last_err)
        self.assertIn("farbe=blau", self.last_out)

    def test_bad_file(self):
        draft = self.write("d.ppatch", self.DRAFT.replace("${farbe}", "neu"), root=self.tmp)
        answers = self.write("x.txt", "nur text\n", root=self.tmp)
        self.assertEqual(self.run_cli("run", "-n", "--answers", answers, draft,
                                      "-p", self.target), 1)
        self.assertIn("Zeile 1: NAME=WERT erwartet", self.last_err)
