"""mode html / xml: Normalform, Einheiten (robuste Adressen), Befehle, create."""

import os
import shutil
import unittest

from helpers import ProjectCase, doc

from pypatcher import adapters, patchfile
from pypatcher import profile as pf
from pypatcher.adapters import ParseError

HTML = pf.complete({"mode": "html"})
XML = pf.complete({"mode": "xml"})

PAGE = (
    "<!DOCTYPE html>\n"
    "<html>\n"
    "<head>\n"
    "  <script>\n"
    "    function f() { return 1; } // Kommentar\n"
    "  </script>\n"
    "</head>\n"
    "<body>\n"
    "  <!-- Navigation -->\n"
    "  <nav id=\"nav\">\n"
    "    <a href=\"/\">Start</a>\n"
    "  </nav>\n"
    "  <main>\n"
    "    <div class=\"card\">\n"
    "      <p>Eins</p>\n"
    "    </div>\n"
    "    <div class=\"card\">\n"
    "      <p>Zwei</p>\n"
    "    </div>\n"
    "  </main>\n"
    "</body>\n"
    "</html>\n"
)

FEED = (
    "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n"
    "<feed>\n"
    "  <entry id=\"e1\">\n"
    "    <title>Eins</title>\n"
    "  </entry>\n"
    "  <entry id=\"e2\">\n"
    "    <title>Zwei</title>\n"
    "  </entry>\n"
    "</feed>\n"
)


def h(text, prof=HTML):
    return adapters.file_hash(doc(text), prof)


class Normalform(unittest.TestCase):
    def test_cosmetics_ignored(self):
        variant = PAGE.replace("<!-- Navigation -->", "<!-- Menue -->") \
            .replace('<a href="/">', "<a  href='/' >").replace("// Kommentar", "/* x */")
        self.assertEqual(h(PAGE), h(variant))
        self.assertEqual(h('<p title="a &amp; b">x</p>\n'), h('<p title="a &#38; b">x</p>\n'))
        self.assertEqual(h('<img alt="A" src="a.png">\n'), h('<img src="a.png" alt="A">\n'))

    def test_whitespace_counts_by_default(self):
        self.assertNotEqual(h(PAGE), h(PAGE.replace("<p>Eins</p>", "<p>Eins </p>")))
        loose = pf.complete({"mode": "html", "whitespace": "ignore"})
        self.assertEqual(h(PAGE, loose), h(PAGE.replace("<p>Eins</p>", "<p>Eins </p>")
                                             .replace("    <a", "\t\t<a"), loose))
        self.assertNotEqual(h("<pre>a  b</pre>\n", loose), h("<pre>a b</pre>\n", loose))

    def test_functional_changes(self):
        self.assertNotEqual(h(PAGE), h(PAGE.replace("Zwei", "Drei")))
        self.assertNotEqual(h(PAGE), h(PAGE.replace("return 1", "return 2")))
        self.assertNotEqual(h(PAGE), h(PAGE.replace('href="/"', 'href="/start"')))

    def test_xml(self):
        self.assertEqual(h(FEED, XML), h(FEED.replace('<entry id="e1">', "<entry  id='e1'>"), XML))
        with self.assertRaises(ParseError):
            adapters.get("xml").validate(doc("<a><b></a>\n"))

    def test_units(self):
        info = adapters.get("html").analyze(doc(PAGE))
        paths = dict((u.path, (u.start, u.end)) for u in info.units)
        self.assertEqual(paths["#nav"], (8, 12))            # inkl. Kommentarzeile
        self.assertIn("html/body/main/div.card[2]", paths)
        self.assertIn("html/head/script", paths)
        xml_units = [u.path for u in adapters.get("xml").analyze(doc(FEED)).units]
        self.assertEqual(xml_units, ["feed", "#e1", "#e1/title", "#e2", "#e2/title"])


class MarkupFlow(ProjectCase):
    def setUp(self):
        super().setUp()
        self.write("index.html", PAGE)
        self.write("feed.xml", FEED)

    def test_commands(self):
        patch = self.seal({"id": "0001-html", "title": "html", "files": [
            {"path": "index.html", "action": "modify", "changes": [
                {"op": "replace_unit", "unit": "html/body/main/div.card[2]",
                 "new": ["<div class=\"card\">", "\t<p>Zwei, neu</p>", "</div>"]},
                {"op": "add_unit", "position": "end-of:html/body/main",
                 "new": ["<div class=\"card\">", "\t<p>Drei</p>", "</div>"]},
                {"op": "add_unit", "position": "start-of:#nav",
                 "new": ["<a href=\"/hilfe\">Hilfe</a>"]},
                {"op": "replace_text", "scope": "html/head/script",
                 "old": ["function f() { return 1; } // Kommentar"],
                 "new": ["function f() { return 2; }"]}]},
            {"path": "feed.xml", "action": "modify", "changes": [
                {"op": "delete_unit", "unit": "#e1"}]}]})
        self.make_target()
        before = self.snapshot(self.target)
        self.assertEqual(self.apply(patch), 0, self.last_err)
        page = self.read("index.html").decode()
        self.assertIn("  <nav id=\"nav\">\n    <a href=\"/hilfe\">Hilfe</a>\n    <a href=\"/\">", page)
        self.assertIn("      <p>Zwei, neu</p>\n    </div>\n    <div class=\"card\">\n"
                      "      <p>Drei</p>\n    </div>\n  </main>\n", page)
        self.assertIn("    function f() { return 2; }\n", page)
        self.assertNotIn("e1", self.read("feed.xml").decode())
        self.assertEqual(self.run_cli("rollback", "-q", "-y", "-p", self.target), 0)
        self.assertEqual(self.snapshot(self.target), before)

    def test_text_whitespace_is_conflict(self):
        patch = self.seal({"id": "0002-html", "title": "x", "files": [
            {"path": "index.html", "action": "modify", "changes": [
                {"op": "replace_unit", "unit": "#nav",
                 "new": ["<nav id=\"nav\">", "</nav>"]}]}]})
        self.make_target()
        self.write("index.html", PAGE.replace(">Start<", "> Start<"), root=self.target)
        self.assertEqual(self.apply(patch), 3)

    def test_create(self):
        new = os.path.join(self.tmp, "new")
        shutil.copytree(self.base, new)
        self.write("index.html", PAGE.replace("<p>Eins</p>", "<p>Eins!</p>"), root=new)
        self.write("feed.xml", FEED.replace("<title>Zwei</title>", "<title>2</title>"), root=new)
        out = os.path.join(self.tmp, "c.ppatch")
        self.assertEqual(self.run_cli("create", "-q", self.base, new, "-o", out, "--id", "c",
                                      "--title", "c"), 0, self.last_err)
        patch = patchfile.load(out)
        units = [ch.get("unit") for e in patch["files"] for ch in e.get("changes", [])]
        self.assertIn("html/body/main/div.card[1]/p", units)
        self.assertIn("#e2/title", units)
        self.make_target()
        self.assertEqual(self.apply(out), 0, self.last_err)
        self.assertEqual(self.snapshot(self.target), self.snapshot(new))


if __name__ == "__main__":
    unittest.main()
