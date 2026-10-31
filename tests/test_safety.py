"""Sicherheit: Versionsverwaltung geschuetzt, keine Wege ueber symbolische Links."""

import os
import unittest

from helpers import ProjectCase


class Safety(ProjectCase):

    def setUp(self):
        super().setUp()
        self.write("a.txt", "x\n")
        self.make_target()

    def run_draft(self, line, dry=False):
        draft = self.write("d.ppatch", "#PPATCH 1 s 1/1\nTITLE t\n%s\n@1 .\nboom\n@.\n" % line,
                           root=self.tmp)
        return self.run_cli("run", "-n" if dry else "-y", draft, "-p", self.target)

    def test_vcs_directories_protected(self):
        for path in (".git/hooks/pre-commit", "sub/.git/config", ".hg/hgrc", ".SVN/x"):
            self.assertEqual(self.run_draft("ADD %s @1" % path), 2, path)
            self.assertIn("Versionsverwaltung", self.last_err)
        self.assertFalse(os.path.exists(os.path.join(self.target, ".git")))

    @unittest.skipIf(not hasattr(os, "symlink") or os.name == "nt", "ohne symbolische Links")
    def test_no_writes_through_symlinks(self):
        outside = os.path.join(self.tmp, "outside")
        os.makedirs(outside)
        os.symlink(outside, os.path.join(self.target, "link"))
        os.symlink(os.path.join(self.target, "a.txt"), os.path.join(self.target, "alias.txt"))
        self.assertEqual(self.run_draft("ADD link/pwned.txt @1"), 3)
        self.assertIn("symbolischen Link", self.last_err)
        self.assertEqual(os.listdir(outside), [])
        self.assertEqual(self.run_draft("REP alias.txt @1"), 3)
        self.assertEqual(self.read("a.txt"), b"x\n")

    def test_help_command(self):
        self.assertEqual(self.run_cli("help"), 0)
        self.assertEqual(self.run_cli("help", "run"), 0)
        self.assertEqual(self.run_cli("help", "gibtsnicht"), 1)
