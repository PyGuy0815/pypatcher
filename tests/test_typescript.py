"""R016: TypeScript, TSX und JSX."""

import os
import shutil

from helpers import ProjectCase, doc

from pypatcher import adapters
from pypatcher.profile import default_mode

TS = adapters.get("ts")
TSX = adapters.get("tsx")

SOURCE = """import { Injectable } from "@angular/core";

export interface User {
  id: number;
  name?: string;
}

export type Id = string | number;

type Handler<T> =
  | ((value: T) => void)
  | null;

export const enum Color {
  Red,
  Green,
}

// Dienst fuer Benutzer
@Injectable({
  providedIn: "root",
})
export class UserService<T extends User> {
  private readonly cache: Map<Id, T> = new Map();

  constructor(private http: Http) {}

  public async load(id: Id): Promise<T | undefined> {
    return this.cache.get(id);
  }

  protected static parse<U>(raw: string): { value: U } {
    return { value: JSON.parse(raw) };
  }

  handler: Handler<T> = (value: T): void => {
    this.cache.set(value.id, value);
  };

  @HostListener("click")
  onClick(): void {
    this.load(1);
  }
}

export function sum<T extends number>(a: T, b: T): number {
  return a + b;
}

export const greet = async <T,>(who: T): Promise<string> => {
  return `Hallo ${who}`;
};

namespace Utils.Strings {
  export function upper(s: string): string {
    return s.toUpperCase();
  }
}

declare module "my-lib" {
  export function helper(): void;
}
"""

JSX = """import React from "react";

export function Hello({ name }: { name: string }) {
  const title = <h1 className="t">Hallo {name}, it's me</h1>;
  return (
    <div>
      {title}
      <p>a &lt; b ist "wahr"</p>
      <Button onClick={() => alert('x')} />
    </div>
  );
}

export const App = () => {
  return <><Hello name="Welt" /></>;
};
"""


class TypeScriptUnits(ProjectCase):

    def test_modes(self):
        self.assertEqual(default_mode("a.ts"), "ts")
        self.assertEqual(default_mode("a.tsx"), "tsx")
        self.assertEqual(default_mode("a.jsx"), "jsx")

    def test_units(self):
        info = TS.analyze(doc(SOURCE))
        units = dict((u.path, u.kind) for u in info.units)
        expected = {
            "User": "interface", "Id": "type", "Handler": "type", "Color": "enum",
            "UserService": "class", "UserService.load": "method",
            "UserService.parse": "method", "UserService.handler": "method",
            "UserService.onClick": "method", "UserService.constructor": "method",
            "sum": "function", "greet": "function", "Utils.Strings": "namespace",
            "Utils.Strings.upper": "function"}
        for path, kind in expected.items():
            self.assertEqual(units.get(path), kind, path)
        service = next(u for u in info.units if u.path == "UserService")
        self.assertEqual(info.lines[service.start], "// Dienst fuer Benutzer")
        on_click = next(u for u in info.units if u.path == "UserService.onClick")
        self.assertEqual(info.lines[on_click.start].strip(), '@HostListener("click")')
        handler = next(u for u in info.units if u.path == "Handler")
        self.assertEqual(info.lines[handler.end - 1], "  | null;")

    def test_jsx(self):
        info = TSX.analyze(doc(JSX))
        self.assertEqual([u.path for u in info.units], ["Hello", "App"])
        self.assertNotEqual(TSX.normalize(doc(JSX), {"comments": "ignore", "whitespace": "ignore"}),
                            TSX.normalize(doc(JSX.replace("it's me", "it is me")),
                                          {"comments": "ignore", "whitespace": "ignore"}))

    def test_comments_and_layout_ignored(self):
        prof = {"comments": "ignore", "whitespace": "ignore"}
        other = SOURCE.replace("  return a + b;", "  // Summe\n      return a+b;")
        self.assertEqual(TS.normalize(doc(SOURCE), prof), TS.normalize(doc(other), prof))

    def test_patch_and_create(self):
        self.write("src/service.ts", SOURCE)
        self.write("src/app.tsx", JSX)
        self.make_target()
        draft = self.write("d.ppatch", "#PPATCH 1 t 1/1\nTITLE t\nFILE src/service.ts\n"
                           "  UNIT UserService.load @1\n  UADD after:Color @2\n"
                           "  UNIT Utils.Strings.upper @3\n"
                           "@1 .\npublic async load(id: Id): Promise<T | undefined> {\n"
                           "  return this.cache.get(id) ?? undefined;\n}\n@.\n"
                           "@2 .\nexport interface Admin extends User {\n  rights: string[];\n}\n@.\n"
                           "@3 .\nexport function upper(s: string): string {\n"
                           "  return s.toLocaleUpperCase();\n}\n@.\n", root=self.tmp)
        self.assertEqual(self.run_cli("run", "-y", draft, "-p", self.target), 0, self.last_err)
        text = self.read("src/service.ts").decode()
        self.assertIn("    return this.cache.get(id) ?? undefined;\n", text)
        self.assertIn("  Green,\n}\n\nexport interface Admin extends User {\n  rights: string[];\n}\n",
                      text)
        self.assertIn("    return s.toLocaleUpperCase();\n", text)
        new = os.path.join(self.tmp, "new")
        shutil.copytree(self.target, new, ignore=shutil.ignore_patterns(".patcher"))
        self.write("src/app.tsx", JSX.replace("Hallo {name}", "Moin {name}"), root=new)
        out = os.path.join(self.tmp, "c.ppatch")
        self.assertEqual(self.run_cli("create", self.base, new, "-o", out, "--id", "c",
                                      "--title", "t"), 0, self.last_err)
        with open(out, encoding="utf-8") as handle:
            text = handle.read()
        self.assertIn("FILE src/app.tsx tsx", text)
        self.assertIn("UNIT Hello", text)
