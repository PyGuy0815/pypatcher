"""Fuenfter Praxistest: Leerzeichen-Einrueckung in Suchbloecken, Pfade mit
Leerzeichen, Patches auf ignorierte Dateien."""

from helpers import ProjectCase

CLEANUP = '''import argparse


def main():
    parser = argparse.ArgumentParser()
    args = parser.parse_args()
    root = args.project
    output = args.report_dir.expanduser().resolve()
    if output == root or root in output.parents:
        parser.error('ausserhalb')
    apply_parser = parser
    apply_parser.set_defaults(func=main)
    return output
'''


class FieldTest5(ProjectCase):

    def setUp(self):
        super().setUp()
        self.write("cleanup.py", CLEANUP)
        self.write("patches/Chaos CompleXX.ips", "PATCH")
        self.make_target()

    def run_draft(self, body, dry=False):
        draft = self.write("d.ppatch", "#PPATCH 1 f 1/1\nTITLE t\n" + body, root=self.tmp)
        return self.run_cli("run", "-n" if dry else "-y", draft, "-p", self.target)

    def test_text_block_with_space_indentation(self):
        code = self.run_draft("FILE cleanup.py\n  TEXT @1 @2\n"
                              "@1 .\noutput = args.report_dir.expanduser().resolve()\n"
                              "if output == root or root in output.parents:\n"
                              "    parser.error('ausserhalb')\n@.\n"
                              "@2 .\noutput = root / 'reports'\nif output == root:\n"
                              "    parser.error('Wurzel')\n@.\n")
        self.assertEqual(code, 0, self.last_err)
        self.assertIn("    output = root / 'reports'\n    if output == root:\n"
                      "        parser.error('Wurzel')\n", self.read("cleanup.py").decode())

    def test_absolute_indentation_in_anchor_and_new(self):
        code = self.run_draft("FILE cleanup.py\n  TINS after @1 @2\n"
                              "@1 .\n    apply_parser.set_defaults(func=main)\n@.\n"
                              "@2 .\n    apply_parser.add_argument(\n        \"--snes-header\",\n"
                              "        default=\"auto\",\n    )\n@.\n")
        self.assertEqual(code, 0, self.last_err)
        self.assertIn("    apply_parser.set_defaults(func=main)\n    apply_parser.add_argument(\n"
                      "        \"--snes-header\",\n        default=\"auto\",\n    )\n    return output\n",
                      self.read("cleanup.py").decode())

    def test_move_with_spaces(self):
        self.assertEqual(self.run_draft("MOVE patches/Chaos CompleXX.ips rom/Chaos CompleXX.ips\n",
                                        dry=True), 2)
        self.assertIn('Pfade mit Leerzeichen in \\"...\\" setzen'.replace('\\"', '"'), self.last_err)
        self.assertEqual(self.run_draft('MOVE "patches/Chaos CompleXX.ips" '
                                        '"rom/Chaos CompleXX.ips"\n'), 0, self.last_err)
        self.assertEqual(self.read("rom/Chaos CompleXX.ips"), b"PATCH")

    def test_hint_for_ignored_files(self):
        self.write(".DS_Store", "x", root=self.target)
        self.assertEqual(self.run_draft("DEL .DS_Store\n", dry=True), 0, self.last_err)
        self.assertIn("werden von pypatcher ignoriert", self.last_out)
