"""R017: JS-Funktionen in Funktionen und Objekt-Methoden als Einheiten."""

from helpers import ProjectCase, doc

from pypatcher.adapters import get

JS = get("js")

SOURCE = """// Dienste
export const api = {
  base: "/api",
  // laedt
  async load(id) {
    return fetch(this.base + id);
  },
  save: function (item) {
    return item;
  },
  check: (x) => {
    return !!x;
  },
  sub: {
    ping() {
      return 1;
    }
  }
};

function outer(a) {
  function inner(b) {
    return b * 2;
  }
  const helper = (c) => {
    return c + 1;
  };
  return inner(helper(a));
}

class Box {
  get(i) {
    function local() {
      return i;
    }
    return local();
  }
}
"""


class JsNested(ProjectCase):

    def test_paths(self):
        info = JS.analyze(doc(SOURCE))
        units = dict((u.path, u.kind) for u in info.units)
        for path, kind in (("api", "object"), ("api.load", "method"), ("api.save", "method"),
                           ("api.check", "method"), ("api.sub", "object"),
                           ("api.sub.ping", "method"), ("outer", "function"),
                           ("outer.inner", "function"), ("outer.helper", "function"),
                           ("Box.get", "method"), ("Box.get.local", "function")):
            self.assertEqual(units.get(path), kind, path)
        self.assertNotIn("api.base", units)
        load = next(u for u in info.units if u.path == "api.load")
        self.assertEqual((load.start, load.end), (3, 7))     # mit Kommentar und ","

    def apply(self, draft):
        self.write("app.js", SOURCE)
        self.make_target()
        path = self.write("d.ppatch", "#PPATCH 1 p 1/1\nTITLE t\nFILE app.js\n" + draft,
                          root=self.tmp)
        self.assertEqual(self.run_cli("run", "-y", path, "-p", self.target), 0, self.last_err)
        return self.read("app.js").decode("utf-8")

    def test_replace_nested_function(self):
        text = self.apply("  UNIT outer.inner @1\n@1 .\nfunction inner(b) {\n\treturn b * 3;\n}\n@.\n")
        self.assertIn("  function inner(b) {\n    return b * 3;\n  }\n  const helper", text)

    def test_object_member_commas(self):
        text = self.apply("  UNIT api.check @1\n  UADD end-of:api @2\n  UDEL api.save\n"
                          "@1 .\ncheck(x) {\n\treturn x > 0;\n}\n@.\n"
                          "@2 .\nreset() {\n\tthis.base = \"\";\n}\n@.\n")
        self.assertIn("  check(x) {\n    return x > 0;\n  },\n", text)
        self.assertIn("    }\n  },\n\n  reset() {\n    this.base = \"\";\n  }\n};", text)
        self.assertNotIn("save", text)

    def test_add_first_member_after_property(self):
        text = self.apply("  UADD before:api.load @1\n@1 .\nfirst() {\n\treturn 0;\n}\n@.\n")
        self.assertIn('  base: "/api",\n\n  first() {\n    return 0;\n  },\n\n  // laedt', text)

    def test_copy_member_with_rename(self):
        text = self.apply("  UCOPY api.save after:api.save as=store\n")
        self.assertIn("  store: function (item) {\n    return item;\n  },\n", text)
