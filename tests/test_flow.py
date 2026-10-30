"""Gesamtablauf: seal -> check/apply -> verify -> rollback/recover."""

import os
import unittest

from helpers import ProjectCase

from pypatcher import journal as jr, patchfile

MODULE = (
    "#!/usr/bin/env python3\n"
    '"""Modul."""\n'
    "\n"
    "import os\n"
    "\n"
    "\n"
    "# addiert\n"
    "def add(a, b):\n"
    "    return a + b\n"
    "\n"
    "\n"
    "class Calc:\n"
    '    """Rechner."""\n'
    "\n"
    "    def run(self, x):\n"
    "        if x:\n"
    "            return 1\n"
    "        return 0\n"
)


class Flow(ProjectCase):
    def setUp(self):
        super().setUp()
        self.write("src/calc.py", MODULE)
        self.write("src/other.py", "def other():\n    return 1\n")
        self.write("conf.ini", "[main]\nkey = 1\n")
        self.write("logo.bin", b"\x00\x01\x02", binary=True)

    def draft(self, files, pid="0001-test", **head):
        draft = {"id": pid, "title": "Test", "files": files}
        draft.update(head)
        return draft

    def roundtrip(self, files, expect=None):
        """seal, apply, Ergebnis pruefen, rollback -> exakt wie vorher."""
        patch = self.seal(self.draft(files))
        self.make_target()
        before = self.snapshot(self.target)
        self.assertEqual(self.apply(patch), 0, self.last_err)
        self.assertEqual(self.run_cli("verify", "-q", "-p", self.target), 0, self.last_out)
        result = self.snapshot(self.target)
        for rel, content in (expect or {}).items():
            self.assertEqual(result.get(rel), content.encode() if content is not None else None,
                             rel)
        self.assertEqual(self.run_cli("rollback", "-q", "-y", "-p", self.target), 0, self.last_err)
        self.assertEqual(self.snapshot(self.target), before)
        self.assertEqual(self.run_cli("verify", "-q", "-p", self.target), 0, self.last_out)
        return result

    # -- Einheiten -----------------------------------------------------------

    def test_replace_unit(self):
        result = self.roundtrip([{"path": "src/calc.py", "action": "modify", "changes": [
            {"op": "replace_unit", "unit": "Calc.run",
             "new": ["def run(self, x):", "\treturn 2 if x else 0"]}]}])
        self.assertIn(b"    def run(self, x):\n        return 2 if x else 0\n",
                      result["src/calc.py"])

    def test_add_and_delete_unit_spacing(self):
        result = self.roundtrip([{"path": "src/calc.py", "action": "modify", "changes": [
            {"op": "add_unit", "position": "after:add", "new": ["def sub(a, b):", "\treturn a - b"]},
            {"op": "add_unit", "position": "end-of:Calc", "new": ["def stop(self):", "\tpass"]},
            {"op": "delete_unit", "unit": "Calc.run"}]}])
        text = result["src/calc.py"].decode()
        self.assertIn("    return a + b\n\n\ndef sub(a, b):\n    return a - b\n\n\nclass Calc:", text)
        self.assertTrue(text.endswith('"""Rechner."""\n\n    def stop(self):\n        pass\n'), text)

    def test_copy_and_move_units(self):
        result = self.roundtrip([
            {"path": "src/other.py", "action": "modify", "changes": [
                {"op": "copy_unit", "from": "src/calc.py::add", "as": "plus", "position": "end"},
                {"op": "move_unit", "from": "src/calc.py::Calc.run", "move_id": "m1",
                 "position": "end"}]},
            {"path": "src/calc.py", "action": "modify", "changes": [
                {"op": "move_unit", "unit": "add", "position": "after:Calc"},
                {"op": "move_unit", "unit": "Calc.run", "to": "src/other.py", "move_id": "m1"}]}])
        other = result["src/other.py"].decode()
        self.assertIn("# addiert\ndef plus(a, b):\n    return a + b\n", other)
        self.assertIn("\n\n\ndef run(self, x):\n    if x:\n        return 1\n", other)
        calc = result["src/calc.py"].decode()
        self.assertNotIn("def run", calc)
        self.assertTrue(calc.endswith('"""Rechner."""\n\n\n# addiert\ndef add(a, b):\n'
                                      "    return a + b\n"), calc)

    def test_move_pairing_required(self):
        draft = self.draft([{"path": "src/other.py", "action": "modify", "changes": [
            {"op": "move_unit", "from": "src/calc.py::add", "move_id": "x", "position": "end"}]}])
        path = self.write_draft(draft)
        self.assertEqual(self.run_cli("seal", path, "--base", self.base, "-o",
                                      os.path.join(self.tmp, "o.ppatch")), 2)

    def test_replace_header(self):
        result = self.roundtrip([{"path": "src/calc.py", "action": "modify", "changes": [
            {"op": "replace_unit", "unit": "@header",
             "new": ["#!/usr/bin/env python3", '"""Neue Doku."""']}]}])
        self.assertTrue(result["src/calc.py"].startswith(b'#!/usr/bin/env python3\n"""Neue Doku."""\n'))

    # -- Text ----------------------------------------------------------------

    def test_text_ops_with_scope(self):
        result = self.roundtrip([
            {"path": "src/calc.py", "action": "modify", "changes": [
                {"op": "replace_text", "scope": "Calc.run",
                 "old": ["if x:", "\treturn 1"], "new": ["if x > 1:", "\treturn 1"]},
                {"op": "insert_text", "scope": "Calc.run", "anchor": ["return 0"],
                 "position": "before", "new": ["print(x)"]},
                {"op": "delete_text", "old": ["import os"]}]},
            {"path": "conf.ini", "action": "modify", "changes": [
                {"op": "insert_text", "anchor": ["[main]"], "position": "after",
                 "new": ["neu = 2"]}]}])
        self.assertIn(b"        if x > 1:\n            return 1\n        print(x)\n        return 0\n",
                      result["src/calc.py"])
        self.assertNotIn(b"import os", result["src/calc.py"])
        self.assertEqual(result["conf.ini"], b"[main]\nneu = 2\nkey = 1\n")

    def test_invalid_intermediate_state(self):
        """Zwischenstand darf ungueltig sein, geprueft wird die Syntax am Dateiende."""
        self.write("doc.py", "a = 1\nb = 2\nc = 3\n")
        patch = self.seal(self.draft([{"path": "doc.py", "action": "modify", "changes": [
            {"op": "replace_text", "old": ["a = 1"], "new": ['a = """']},
            {"op": "replace_text", "old": ["c = 3"], "new": ['"""']}]}]),
            "z.ppatch", "--verify", "full")
        self.make_target()
        self.assertEqual(self.apply(patch), 0, self.last_err)
        self.assertEqual(self.read("doc.py"), b'a = """\nb = 2\n"""\n')

    def test_stale_bytecode_removed(self):
        """Ersetzte .py-Datei: zugehoeriger Bytecode wird entfernt (gleiche Groesse,
        gleiche Sekunde wuerde Python sonst alten Code ausfuehren lassen)."""
        self.write("ver.py", 'V = "a1"\n')
        patch = self.seal(self.draft([{"path": "ver.py", "action": "modify", "changes": [
            {"op": "replace_text", "old": ['V = "a1"'], "new": ['V = "a2"']}]}]), "v.ppatch")
        self.make_target()
        cache = os.path.join(self.target, "__pycache__")
        os.makedirs(cache)
        stale = os.path.join(cache, "ver.cpython-99.pyc")
        with open(stale, "wb") as handle:
            handle.write(b"alt")
        self.assertEqual(self.apply(patch), 0, self.last_err)
        self.assertFalse(os.path.exists(stale))

    def test_ambiguous_block_is_conflict(self):
        self.write("dup.txt", "a\nx\na\n")
        draft = self.draft([{"path": "dup.txt", "action": "modify", "changes": [
            {"op": "replace_text", "old": ["a"], "new": ["b"]}]}])
        path = self.write_draft(draft)
        self.assertEqual(self.run_cli("seal", path, "--base", self.base, "-o",
                                      os.path.join(self.tmp, "o.ppatch")), 3)
        self.assertIn("nicht eindeutig", self.last_err)

    # -- Dateiaktionen -------------------------------------------------------

    def test_new_file_permissions(self):
        patch = self.seal(self.draft([{"path": "neu.txt", "action": "add", "new": ["x"]}]))
        self.make_target()
        self.assertEqual(self.apply(patch), 0, self.last_err)
        mask = os.umask(0)
        os.umask(mask)
        mode = os.stat(os.path.join(self.target, "neu.txt")).st_mode & 0o777
        self.assertEqual(mode, 0o666 & ~mask)
        self.assertEqual(os.stat(patch).st_mode & 0o777, 0o666 & ~mask)

    def test_file_actions(self):
        src = self.write("source/neu.bin", b"\xff\x00neu", binary=True, root=self.tmp)
        result = self.roundtrip([
            {"path": "new/hello.py", "action": "add", "new": ["def hello():", "\treturn 1"]},
            {"path": "conf.ini", "action": "move", "to": "cfg/conf.ini"},
            {"path": "src/other.py", "action": "copy", "to": "src/other2.py"},
            {"path": "logo.bin", "action": "replace", "source": os.path.relpath(src, self.tmp)},
            {"path": "src/calc.py", "action": "delete"}])
        self.assertEqual(result["new/hello.py"], b"def hello():\n    return 1\n")
        self.assertEqual(result["cfg/conf.ini"], b"[main]\nkey = 1\n")
        self.assertNotIn("conf.ini", result)
        self.assertIn("src/other2.py", result)
        self.assertEqual(result["logo.bin"], b"\xff\x00neu")
        self.assertNotIn("src/calc.py", result)
        self.assertFalse(os.path.exists(os.path.join(self.target, "new")))

    # -- Pruefungen ----------------------------------------------------------

    def _simple_patch(self):
        return self.seal(self.draft([{"path": "src/calc.py", "action": "modify", "changes": [
            {"op": "replace_unit", "unit": "add", "new": ["def add(a, b):", "\treturn b + a"]}]}]))

    def test_conflict_writes_nothing(self):
        patch = self._simple_patch()
        self.make_target()
        self.write("src/calc.py", MODULE.replace("return 0", "return -1"), root=self.target)
        before = self.snapshot(self.target)
        self.assertEqual(self.apply(patch), 3)
        self.assertEqual(self.snapshot(self.target), before)

    def test_unit_match_mode(self):
        patch = self._simple_patch()
        self.make_target()
        self.write("src/calc.py", MODULE.replace("return 0", "return -1"), root=self.target)
        self.assertEqual(self.apply(patch, "--match", "unit"), 0, self.last_err)

    def test_cosmetic_difference(self):
        patch = self.seal(self.draft([{"path": "src/calc.py", "action": "modify", "changes": [
            {"op": "replace_unit", "unit": "add", "new": ["def add(a, b):", "\treturn b + a"]}]}]),
            "full.ppatch", "--verify", "full")
        self.make_target()
        self.write("src/calc.py", MODULE.replace("# addiert", "# anders"), root=self.target)
        self.assertEqual(self.run_cli("check", "--exact", patch, "-p", self.target), 3)
        self.assertEqual(self.run_cli("check", patch, "-p", self.target), 0)
        self.assertIn("kosmetische Abweichung", self.last_out)

    def test_tampered_patch_rejected(self):
        patch = self._simple_patch()
        self.make_target()
        with open(patch, encoding="utf-8") as handle:
            text = handle.read()
        with open(patch, "w", encoding="utf-8") as handle:
            handle.write(text.replace("\treturn b + a", "\treturn 42"))
        self.assertEqual(self.apply(patch), 2)
        self.assertIn("Patch-Hash", self.last_err)

    def test_reapply_and_requires(self):
        patch = self._simple_patch()
        self.make_target()
        self.assertEqual(self.apply(patch), 0)
        self.assertEqual(self.apply(patch), 3)
        second = self.seal(self.draft([{"path": "conf.ini", "action": "delete"}],
                                      pid="0002", requires=["0009-fehlt"]), "q.ppatch")
        self.assertEqual(self.apply(second), 3)
        self.assertIn("benoetigt", self.last_err)

    def test_layout_mismatch_is_conflict(self):
        patch = self._simple_patch()
        self.make_target()
        self.write("src/calc.py", MODULE.replace("    ", "\t"), root=self.target)
        self.assertEqual(self.apply(patch), 3)

    def test_crlf_preserved(self):
        patch = self._simple_patch()
        self.make_target()
        self.write("src/calc.py", MODULE.replace("\n", "\r\n"), root=self.target)
        self.assertEqual(self.apply(patch), 0, self.last_err)
        data = self.read("src/calc.py")
        self.assertIn(b"def add(a, b):\r\n    return b + a\r\n", data)
        self.assertEqual(data.count(b"\n"), data.count(b"\r\n"))

    def test_state_match(self):
        patch = self.seal(self.draft([{"path": "conf.ini", "action": "delete"}], match="state"))
        self.make_target()
        self.write("extra.txt", "x", root=self.target)
        self.assertEqual(self.apply(patch), 3)
        os.remove(os.path.join(self.target, "extra.txt"))
        self.assertEqual(self.apply(patch), 0, self.last_err)

    # -- Journal -------------------------------------------------------------

    def test_verify_reports(self):
        self.make_target()
        self.write("src/other.py", "def other():\n    return 1  # neu\n", root=self.target)
        self.assertEqual(self.run_cli("verify", "-p", self.target), 0)
        self.assertIn("kosmetisch", self.last_out)
        self.write("src/other.py", "def other():\n    return 2\n", root=self.target)
        self.write("fremd.txt", "x", root=self.target)
        self.assertEqual(self.run_cli("verify", "-p", self.target), 3)
        self.assertIn("funktional", self.last_out)
        self.assertIn("fremd.txt", self.last_out)

    def test_rollback_to(self):
        first = self._simple_patch()
        second = self.seal(self.draft([{"path": "conf.ini", "action": "delete"}], pid="0002"),
                           "q.ppatch")
        self.make_target()
        before = self.snapshot(self.target)
        self.assertEqual(self.apply(first), 0)
        self.assertEqual(self.apply(second), 0, self.last_err)
        self.assertEqual(self.run_cli("rollback", "-y", "--to", "0001-test", "-p", self.target), 0)
        self.assertEqual(jr.Journal(self.target).applied_ids(), ["0001-test"])
        self.assertEqual(self.run_cli("rollback", "-y", "-p", self.target), 0)
        self.assertEqual(self.snapshot(self.target), before)

    def test_rollback_refused_after_change(self):
        patch = self._simple_patch()
        self.make_target()
        self.assertEqual(self.apply(patch), 0)
        self.write("src/calc.py", MODULE.replace("return 0", "return 5"), root=self.target)
        self.assertEqual(self.run_cli("rollback", "-y", "-p", self.target), 3)

    def test_recover_after_crash(self):
        patch = self._simple_patch()
        self.make_target()
        before = self.snapshot(self.target)
        original = patchfile.dumps

        def crash(_patch):
            raise RuntimeError("Absturz")
        patchfile.dumps = crash
        try:
            self.apply(patch)
        finally:
            patchfile.dumps = original
        self.assertNotEqual(self.snapshot(self.target), before)
        self.assertEqual(self.apply(patch), 4)
        self.assertEqual(self.run_cli("recover", "-p", self.target), 0)
        self.assertEqual(self.snapshot(self.target), before)
        self.assertEqual(self.apply(patch), 0, self.last_err)

    def test_example_draft(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        out = os.path.join(self.tmp, "beispiel.ppatch")
        self.assertEqual(self.run_cli("seal", "-q", os.path.join(root, "examples", "entwurf-beispiel.ppatch"),
                                      "--base", os.path.join(root, "examples", "demo"), "-o", out),
                         0, self.last_err)

    def test_show_and_units(self):
        patch = self._simple_patch()
        self.assertEqual(self.run_cli("show", patch), 0)
        self.assertIn("Patch-Hash: OK", self.last_out)
        self.assertEqual(self.run_cli("units", os.path.join(self.base, "src", "calc.py")), 0)
        self.assertIn("Calc.run", self.last_out)


if __name__ == "__main__":
    unittest.main()
