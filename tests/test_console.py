"""R030: farbige Ausgabe nur am Terminal bzw. mit --color always."""

import io
import os
import unittest
from unittest import mock

from helpers import ProjectCase

from pypatcher import console


class Tty(io.StringIO):
    def isatty(self):
        return True


class Colors(unittest.TestCase):

    def setUp(self):
        self.mode = console.MODE
        console.MODE = "auto"

    def tearDown(self):
        console.MODE = self.mode

    def test_auto_only_on_terminal(self):
        with mock.patch.dict(os.environ, {"NO_COLOR": "", "TERM": "xterm"}), \
                mock.patch.object(console.os, "name", "posix"):
            self.assertEqual(console.paint("x", "ok", io.StringIO()), "x")
            self.assertEqual(console.paint("x", "ok", Tty()), "\033[1;32mx\033[0m")

    def test_no_color_and_dumb_terminal(self):
        for env in ({"NO_COLOR": "1", "TERM": "xterm"}, {"NO_COLOR": "", "TERM": "dumb"}):
            with mock.patch.dict(os.environ, env):
                self.assertEqual(console.paint("x", "error", Tty()), "x")

    def test_always_and_never(self):
        console.MODE = "always"
        self.assertEqual(console.paint("x", "warn", io.StringIO()), "\033[1;33mx\033[0m")
        console.MODE = "never"
        self.assertEqual(console.paint("x", "warn", Tty()), "x")

    def test_labels_and_diff(self):
        console.MODE = "always"
        text = console.label("FEHLER: Block nicht gefunden\n    aehnlichste Stelle:\n"
                             "      -return 2\n      +return 1")
        self.assertTrue(text.startswith("\033[1;31mFEHLER\033[0m: Block"))
        self.assertIn("      \033[31m-return 2\033[0m", text)
        self.assertIn("      \033[32m+return 1\033[0m", text)
        self.assertEqual(console.label("Hinweis: x"), "\033[1;36mHinweis\033[0m: x")
        self.assertEqual(console.label("OKAY"), "OKAY")


class CommandLine(ProjectCase):

    def test_color_option(self):
        self.write("a.py", "x = 1\n")
        self.make_target()
        draft = self.write("d.ppatch", "#PPATCH 1 p 1/1\nTITLE t\nREP a.py @1\n@1 .\nx = 2\n@.\n",
                           root=self.tmp)
        try:
            self.assertEqual(self.run_cli("run", "-y", "-q", "--color", "always", draft,
                                          "-p", self.target), 0, self.last_err)
        finally:
            console.MODE = "auto"
        self.assertEqual(self.last_out.strip(),
                         "\033[1;32mOK\033[0m p | 1 Datei | Journal OK")
        self.assertEqual(self.run_cli("verify", "-p", self.target), 0)
        self.assertNotIn("\033[", self.last_out)
