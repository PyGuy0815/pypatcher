"""R034: Lua."""

from helpers import ProjectCase, doc

from pypatcher import adapters
from pypatcher.adapters.base import ParseError

LUA = adapters.get("lua")

SOURCE = '''local json = require("json")

local M = {}

--- Addiert
function M.add(a, b)
  local function helper(x)
    return x * 2
  end
  return helper(a) + b
end

function M:greet(name)
  if name then
    print("Hi " .. name)
  end
  for i = 1, 3 do print(i) end
  repeat local x = 1 until true
end

local function private(s)
  return [==[lang
  ]] end]==] .. s
end

local handlers = {
  click = function(e)
    return e
  end,
  count = 3
}

return M
'''


class LuaUnits(ProjectCase):

    def test_units(self):
        info = LUA.analyze(doc(SOURCE))
        units = dict((u.path, u.kind) for u in info.units)
        for path, kind in (("M", "table"), ("M.add", "function"), ("M.add.helper", "function"),
                           ("M:greet", "function"), ("private", "function"),
                           ("handlers", "table"), ("handlers.click", "function")):
            self.assertEqual(units.get(path), kind, path)
        add = next(u for u in info.units if u.path == "M.add")
        self.assertEqual(info.lines[add.start], "--- Addiert")

    def test_long_strings_and_errors(self):
        prof = {"comments": "ignore", "whitespace": "ignore"}
        self.assertEqual(LUA.normalize(doc(SOURCE), prof),
                         LUA.normalize(doc(SOURCE.replace("--- Addiert", "--[[ lang\n]]")), prof))
        with self.assertRaises(ParseError):
            LUA.validate(doc("function f()\n  if x then\nend\n"))
        with self.assertRaises(ParseError):
            LUA.validate(doc("local s = [[offen\n"))

    def test_patch(self):
        self.write("mod.lua", SOURCE)
        self.make_target()
        draft = self.write("d.ppatch", "#PPATCH 1 l 1/1\nTITLE t\nFILE mod.lua\n"
                           "  UNIT M:greet @1\n  UADD end-of:handlers @2\n"
                           "  UCOPY M.add after:M.add as=sum\n"
                           "@1 .\nfunction M:greet(name)\n  print(\"Hallo \" .. (name or \"?\"))\nend\n@.\n"
                           "@2 .\nkey = function(e)\n  return e.key\nend\n@.\n", root=self.tmp)
        self.assertEqual(self.run_cli("run", "-y", draft, "-p", self.target), 0, self.last_err)
        text = self.read("mod.lua").decode()
        self.assertIn('function M:greet(name)\n  print("Hallo " .. (name or "?"))\nend\n', text)
        self.assertIn("  count = 3,\n\n  key = function(e)\n    return e.key\n  end\n}\n", text)
        self.assertIn("--- Addiert\nfunction M.sum(a, b)\n", text)
