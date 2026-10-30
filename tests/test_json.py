"""mode json: Normalform, Einheiten (Pfade), Kommas, Befehle, create."""

import os
import shutil
import unittest

from helpers import ProjectCase, doc

from pypatcher import adapters, patchfile
from pypatcher import profile as pf
from pypatcher.adapters import ParseError

J = pf.complete({"mode": "json"})

CONF = (
    "{\n"
    "  \"name\": \"app\",\n"
    "  \"server\": {\n"
    "    \"host\": \"localhost\",\n"
    "    \"port\": 8080\n"
    "  },\n"
    "  \"plugins\": [\n"
    "    \"a\",\n"
    "    \"b\"\n"
    "  ]\n"
    "}\n"
)


def h(text, prof=J):
    return adapters.file_hash(doc(text), prof)


class Normalform(unittest.TestCase):
    def test_data_not_format(self):
        compact = '{"server":{"port":8080,"host":"localhost"},"plugins":["a","b"],"name":"app"}'
        self.assertEqual(h(CONF), h(compact))
        self.assertNotEqual(h(CONF), h(CONF.replace("8080", "8081")))
        self.assertNotEqual(h('{"a": 1}'), h('{"a": 1.0}'))
        self.assertNotEqual(h('{"a": [1, 2]}'), h('{"a": [2, 1]}'))
        keep = pf.complete({"mode": "json", "keyorder": "keep"})
        self.assertNotEqual(h('{"a": 1, "b": 2}', keep), h('{"b": 2, "a": 1}', keep))
        with self.assertRaises(ParseError):
            adapters.get("json").validate(doc('{"a": 1,}\n'))

    def test_units(self):
        paths = [u.path for u in adapters.get("json").analyze(doc(CONF)).units]
        self.assertEqual(paths, ["name", "server", "server/host", "server/port", "plugins",
                                 "plugins/0", "plugins/1"])


class JsonFlow(ProjectCase):
    def setUp(self):
        super().setUp()
        self.write("conf.json", CONF)

    def apply_changes(self, changes):
        patch = self.seal({"id": "0001-j", "title": "j", "files": [
            {"path": "conf.json", "action": "modify", "changes": changes}]})
        self.make_target()
        self.assertEqual(self.apply(patch), 0, self.last_err)
        return self.read("conf.json").decode()

    def test_commas_on_insert_and_delete(self):
        result = self.apply_changes([
            {"op": "replace_unit", "unit": "server/port", "new": ['"port": 9090']},
            {"op": "add_unit", "position": "end-of:server", "new": ['"tls": true']},
            {"op": "add_unit", "position": "end", "new": ['"debug": false']},
            {"op": "delete_unit", "unit": "plugins/1"}])
        self.assertIn('"port": 9090,\n    "tls": true\n  },', result)
        self.assertIn('    "a"\n  ],\n  "debug": false\n}\n', result)
        adapters.get("json").validate(doc(result))

    def test_rename_copy(self):
        result = self.apply_changes([
            {"op": "copy_unit", "from": "server", "as": "backup", "position": "after:server"}])
        self.assertIn('  },\n  "backup": {\n    "host": "localhost",', result)

    def test_create(self):
        new = os.path.join(self.tmp, "new")
        shutil.copytree(self.base, new)
        self.write("conf.json", CONF.replace("8080", "8081"), root=new)
        out = os.path.join(self.tmp, "c.ppatch")
        self.assertEqual(self.run_cli("create", "-q", self.base, new, "-o", out, "--id", "c",
                                      "--title", "c"), 0, self.last_err)
        changes = patchfile.load(out)["files"][0]["changes"]
        self.assertEqual([c.get("unit") for c in changes], ["server/port"])
        self.make_target()
        self.assertEqual(self.apply(out), 0, self.last_err)
        self.assertEqual(self.snapshot(self.target), self.snapshot(new))


if __name__ == "__main__":
    unittest.main()
