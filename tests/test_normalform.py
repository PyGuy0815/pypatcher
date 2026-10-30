"""Normalform und Vergleichsprofile (manifest.txt Abschnitt 5)."""

import unittest

from helpers import doc

from pypatcher import adapters, fileio
from pypatcher import profile as pf
from pypatcher.errors import PatchInvalid

PY = pf.complete({"mode": "python"})
TEXT = pf.complete({"mode": "text"})

BASE = (
    "class A:\n"
    "    def f(self, x):\n"
    "        # Kommentar\n"
    "        return x + 16\n"
)


def h(text, prof=PY):
    return adapters.file_hash(doc(text), prof)


class PythonNormalform(unittest.TestCase):
    def test_comments_and_blank_lines_ignored(self):
        variant = (
            "class A:  # Klasse\n"
            "\n"
            "    def f( self,x ):\n"
            "        return (x+0x10)   # anders\n"
        )
        self.assertEqual(h(BASE), h(variant))

    def test_quotes_and_line_breaks_in_brackets_ignored(self):
        a = "x = f('a', 'b')\n"
        b = 'x = f(\n    "a",\n    "b",\n)\n'
        self.assertEqual(h(a), h(b))

    def test_functional_change_detected(self):
        self.assertNotEqual(h(BASE), h(BASE.replace("x + 16", "x + 17")))

    def test_indent_depth_is_exact(self):
        deeper = BASE.replace("    def", "        def").replace("        # K", "            # K") \
            .replace("        return", "            return")
        self.assertNotEqual(h(BASE), h(deeper))

    def test_indent_composition_is_exact(self):
        tabs = "class A:\n\tdef f(self, x):\n\t\treturn x + 16\n"
        self.assertNotEqual(h(BASE), h(tabs))

    def test_docstrings_count_by_default(self):
        a = 'def f():\n    """Hilfe."""\n    return 1\n'
        b = 'def f():\n    """Andere Hilfe."""\n    return 1\n'
        self.assertNotEqual(h(a), h(b))
        ignore = pf.complete({"mode": "python", "docstrings": "ignore"})
        self.assertEqual(h(a, ignore), h(b, ignore))

    def test_shebang_is_functional(self):
        self.assertNotEqual(h("#!/usr/bin/python3\nx = 1\n"), h("#!/usr/bin/python2\nx = 1\n"))

    def test_comments_keep_profile(self):
        keep = pf.complete({"mode": "python", "comments": "keep"})
        self.assertNotEqual(h(BASE, keep), h(BASE.replace("Kommentar", "anders"), keep))

    def test_line_endings_and_bom(self):
        lf = fileio.decode(BASE.encode(), "utf-8", "t")
        crlf = fileio.decode(b"\xef\xbb\xbf" + BASE.replace("\n", "\r\n").encode(), "utf-8", "t")
        self.assertEqual(adapters.file_hash(lf, PY), adapters.file_hash(crlf, PY))
        keep = pf.complete({"mode": "python", "eol": "keep"})
        self.assertNotEqual(adapters.file_hash(lf, keep), adapters.file_hash(crlf, keep))


# Referenzwert: muss auf allen Python-Versionen (>= 3.8) identisch sein (N7).
GOLDEN_SOURCE = (
    '#!/usr/bin/env python3\n'
    '"""Doku."""\n'
    'import os\n'
    'x = a[1]; y = a[1:2, 3]; z = a[::2]\n'
    'f = f"{x!r:>{w}} und {y}"\n'
    'async def g(*a, b=1, **k) -> int:\n'
    '    async with c as d:\n'
    '        await e\n'
    '    return [i async for i in h if i]\n'
    '@dec(1)\n'
    'class K(B, metaclass=M):\n'
    '    def m(self, /, p, *, q):\n'
    '        try:\n'
    '            pass\n'
    '        except (A, B) as err:\n'
    '            raise X from err\n'
    '        finally:\n'
    '            lambda: (yield)\n'
)
GOLDEN_HASH = "sha256:32bcc0bb3ff16598ccf25ce827f79a5c3d87132b7a327187685d0f940082dd97"


class VersionStability(unittest.TestCase):
    def test_golden_hash(self):
        self.assertEqual(h(GOLDEN_SOURCE), GOLDEN_HASH)

    def test_fstring_parts_unified(self):
        self.assertEqual(h('s = f"{x:>{w}}"\n'), h('s = f"{x:>{w}}" ""\n'))
        self.assertNotEqual(h('s = f"a{x}"\n'), h('s = f"b{x}"\n'))


class TextAndRaw(unittest.TestCase):
    def test_text_trailing_whitespace_ignored(self):
        self.assertEqual(h("a = 1\nb\n", TEXT), h("a = 1   \nb\n\n\n", TEXT))
        self.assertNotEqual(h("a = 1\n", TEXT), h("  a = 1\n", TEXT))
        keep = pf.complete({"mode": "text", "whitespace": "keep"})
        self.assertNotEqual(h("a = 1\n", keep), h("a = 1 \n", keep))

    def test_text_comment_prefix(self):
        prof = pf.complete({"mode": "text", "comments": "ignore", "comment_prefix": "#"})
        self.assertEqual(h("# x\na=1\n", prof), h("# y\na=1\n", prof))
        with self.assertRaises(PatchInvalid):
            pf.complete({"mode": "text", "comments": "ignore"})

    def test_raw_is_binary_exact(self):
        prof = pf.complete({"mode": "raw"})
        a = adapters.file_hash(fileio.Doc(raw=b"a\n"), prof)
        b = adapters.file_hash(fileio.Doc(raw=b"a\r\n"), prof)
        self.assertNotEqual(a, b)

    def test_profile_rules(self):
        with self.assertRaises(PatchInvalid):
            pf.complete({"mode": "python", "indent": "ignore"})
        with self.assertRaises(PatchInvalid):
            pf.complete({"mode": "raw", "comments": "ignore"})
        # Patch-Vorgabe schlaegt Dateiendung, Dateieintrag schlaegt Patch-Vorgabe
        self.assertEqual(pf.resolve("x.py", {"whitespace": "keep"}, {"mode": "text"})["mode"], "text")
        self.assertEqual(pf.resolve("x.py", {"mode": "python"}, {"mode": "text"})["mode"], "python")
        self.assertEqual(pf.resolve("x.cfg")["mode"], "text")


if __name__ == "__main__":
    unittest.main()
