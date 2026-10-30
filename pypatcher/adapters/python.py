"""mode python - Einheiten und Normalform python/1 (manifest.txt 5.5, 7.3).

Normalform = Syntaxbaum (versionsfest serialisiert, ohne Kommentare und
Formatierung) + Einrueckungs-Signatur (exakter Einrueckungs-Text jeder
Anweisungszeile, E13).
"""

import ast
import re
import sys
from collections import Counter

from ..errors import PatchInvalid
from ..layout import leading_ws, detect_generic
from .base import Adapter, Info, ParseError, Unit

CODING_RE = re.compile(r"^[ \t\f]*#.*?coding[:=][ \t]*([-\w.]+)")
DEF_TYPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
IMPORT_TYPES = (ast.Import, ast.ImportFrom)
IMPORTS = "@imports"
DOC_OWNERS = (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
SKIP_FIELDS = frozenset(("ctx", "type_comment", "kind"))
if sys.version_info < (3, 9):
    _INDEX, _EXTSLICE = ast.Index, ast.ExtSlice
else:
    _INDEX = _EXTSLICE = None


def is_docstring(stmt):
    return (isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant)
            and isinstance(stmt.value.value, str))


def _const(value):
    return "%s:%r" % (type(value).__name__, value)


def _fstring_parts(values):
    """f-String-Teile vereinheitlichen: leere Texte weg, benachbarte Texte
    zusammenfassen (Python 3.12 erzeugt z.B. bei {x:>{w}} ein leeres Teil)."""
    out = []
    for value in values:
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            if not value.value:
                continue
            if out and isinstance(out[-1], ast.Constant) and isinstance(out[-1].value, str):
                out[-1] = ast.Constant(value=out[-1].value + value.value)
                continue
            value = ast.Constant(value=value.value)
        out.append(value)
    return out


def _ser(node, skip):
    """Versionsfeste Serialisierung (statt ast.dump)."""
    if isinstance(node, list):
        return "[" + ",".join(_ser(x, skip) for x in node if id(x) not in skip) + "]"
    if not isinstance(node, ast.AST):
        return repr(node)
    if _INDEX is not None and isinstance(node, _INDEX):
        return _ser(node.value, skip)
    if _EXTSLICE is not None and isinstance(node, _EXTSLICE):
        return "Tuple(elts=" + _ser(node.dims, skip) + ")"
    if isinstance(node, ast.Constant):
        return "Constant(" + _const(node.value) + ")"
    if isinstance(node, ast.JoinedStr):
        return "JoinedStr(values=" + _ser(_fstring_parts(node.values), skip) + ")"
    parts = []
    for field in sorted(node._fields):
        if field in SKIP_FIELDS:
            continue
        value = getattr(node, field, None)
        if value is None or (isinstance(value, list) and not value):
            continue
        parts.append(field + "=" + _ser(value, skip))
    return type(node).__name__ + "(" + ",".join(parts) + ")"


def _indent_sig(nodes, lines, skip):
    """Exakter Einrueckungs-Text aller Anweisungszeilen in Zeilenreihenfolge."""
    items = []
    for node in nodes:
        if id(node) in skip:
            continue
        if isinstance(node, (ast.stmt, ast.excepthandler)):
            line = lines[node.lineno - 1]
            prefix = line.encode("utf-8")[:node.col_offset].decode("utf-8", "replace")
            if not prefix.strip():
                items.append((node.lineno, node.col_offset, prefix))
            for deco in getattr(node, "decorator_list", ()):
                dline = lines[deco.lineno - 1]
                ws = leading_ws(dline)
                if dline[len(ws):].startswith("@"):
                    items.append((deco.lineno, -1, ws))
    items.sort()
    return "|".join(repr(p) for _, _, p in items)


def _head_lines(lines):
    """Shebang und Encoding-Zeile - funktional (bestimmen Interpreter/Dekodierung)."""
    out = []
    if lines and lines[0].startswith("#!"):
        out.append(lines[0])
    for i in (0, 1):
        if i < len(lines) and CODING_RE.match(lines[i]):
            out.append(lines[i])
    return "\n".join(out)


RENAME_RE = re.compile(r"^(\t*(?:async[ \t]+)?(?:def|class)[ \t]+)([A-Za-z_]\w*)")
RENAME_ASSIGN_RE = re.compile(r"^(\t*)([A-Za-z_]\w*)(?=[ \t]*(?::|=(?!=)))")
SIMPLE_RE = re.compile(r"^[ \t]*(?:@|(?:async[ \t]+)?def\b|class\b)")


def _target(stmt):
    """Name einer Zuweisung mit genau einem einfachen Ziel (R005), sonst None:
    X = ..., X: int = ..., X: int (z.B. Felder einer dataclass)."""
    if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1:
        target = stmt.targets[0]
    elif isinstance(stmt, ast.AnnAssign) and stmt.simple:
        target = stmt.target
    else:
        return None
    return target.id if isinstance(target, ast.Name) else None


class PythonAdapter(Adapter):
    mode = "python"
    label = "Python"
    has_units = True
    default_indent = "    "
    gap_top = 2
    gap_nested = 1

    def opens_block(self, line):
        return line.rstrip().endswith(":")

    def can_contain(self, unit):
        return unit.kind == "class"

    def rename(self, rel_lines, name):
        if not re.match(r"^[A-Za-z_]\w*$", name or ""):
            raise PatchInvalid("as=%r ist kein gueltiger Name" % name)
        for i, line in enumerate(rel_lines):
            match = RENAME_RE.match(line)
            if match:
                return rel_lines[:i] + [match.group(1) + name + line[match.end(2):]] + rel_lines[i + 1:]
        for i, line in enumerate(rel_lines):
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            match = RENAME_ASSIGN_RE.match(line)
            if match:
                return rel_lines[:i] + [match.group(1) + name + line[match.end(2):]] + rel_lines[i + 1:]
            break
        raise PatchInvalid("keine def/class-Zeile bzw. Zuweisung zum Umbenennen gefunden")

    def simple(self, line):
        """Zeile einer kleinen Einheit (Zuweisung/Import) statt def/class:
        zwischen solchen Zeilen kein Leerzeilen-Abstand beim Einfuegen."""
        return bool(line.strip()) and not SIMPLE_RE.match(line)

    def parse(self, doc):
        source = "\n".join(doc.lines) + "\n"
        try:
            return ast.parse(source)
        except (SyntaxError, ValueError) as exc:
            raise ParseError("Zeile %s: %s" % (getattr(exc, "lineno", "?"), getattr(exc, "msg", exc)))

    def validate(self, doc):
        self.parse(doc)

    # -- Einheiten ---------------------------------------------------------

    def analyze(self, doc):
        tree = self.parse(doc)
        lines = doc.lines
        header_end, header_doc = self._header(lines, tree)
        units = [Unit("@header", "header", header_doc, 0, header_end, "", None)]
        imports = self._imports(tree, lines, header_end, header_doc)
        if imports is not None:
            units.append(imports)
        self._collect(tree.body, "", None, lines, header_end, units)
        return Info(lines, units, tree=tree, header_end=header_end, header_doc=header_doc)

    def _header(self, lines, tree):
        i = 0
        if lines and lines[0].startswith("#!"):
            i = 1
        if i < len(lines) and i <= 1 and CODING_RE.match(lines[i]):
            i += 1
        while i < len(lines) and lines[i].startswith("#"):
            i += 1
        end = i
        body = tree.body
        if body and is_docstring(body[0]):
            between = lines[end:body[0].lineno - 1]
            if all(not l.strip() or l.startswith("#") for l in between):
                return body[0].end_lineno, body[0]
        return end, None

    def _imports(self, tree, lines, header_end, header_doc):
        """@imports: zusammenhaengende Import-Anweisungen am Modulanfang (R005)."""
        body = [st for st in tree.body if st is not header_doc]
        run = []
        for st in body:
            if not isinstance(st, IMPORT_TYPES):
                break
            run.append(st)
        if not run:
            return None
        start = run[0].lineno - 1
        while start - 1 >= header_end and lines[start - 1].startswith("#"):
            start -= 1
        node = ast.Module(body=run, type_ignores=[])
        return Unit(IMPORTS, "imports", node, start, run[-1].end_lineno, "", None)

    def _collect(self, body, prefix, parent, lines, header_end, out):
        named = []
        for st in body:
            if isinstance(st, DEF_TYPES):
                named.append((st.name, st))
            elif _target(st) is not None:
                named.append((_target(st), st))
        counts = Counter(name for name, _ in named)
        seen = Counter()
        for name, st in named:
            seen[name] += 1
            label = name if counts[name] == 1 else "%s#%d" % (name, seen[name])
            path = prefix + "." + label if prefix else label
            first = min([d.lineno for d in getattr(st, "decorator_list", ())] + [st.lineno]) - 1
            indent = leading_ws(lines[first])
            floor = header_end if parent is None else parent.node.lineno
            start = first
            while (start - 1 >= floor and lines[start - 1].strip().startswith("#")
                   and leading_ws(lines[start - 1]) == indent):
                start -= 1
            if isinstance(st, ast.ClassDef):
                kind = "class"
            elif not isinstance(st, DEF_TYPES):
                kind = "assign"
            else:
                kind = "method" if parent is not None and parent.kind == "class" else "function"
            unit = Unit(path, kind, st, start, st.end_lineno, indent, parent)
            out.append(unit)
            if isinstance(st, DEF_TYPES):
                self._collect(st.body, path, unit, lines, header_end, out)

    def position(self, info, pos):
        """-> (zeilenindex, grundtiefe, oberste_ebene) fuer Positionsangaben (7.5)."""
        lines = info.lines
        if pos == "start":
            idx = info.header_end
            for st in info.tree.body:
                if st is info.header_doc:
                    continue
                if isinstance(st, (ast.Import, ast.ImportFrom)):
                    idx = max(idx, st.end_lineno)
                    continue
                break
            return idx, "", True
        if pos == "end":
            idx = len(lines)
            while idx > 0 and not lines[idx - 1].strip():
                idx -= 1
            return idx, "", True
        kind, _, target = pos.partition(":")
        if not target or kind not in ("after", "before", "start-of", "end-of"):
            raise PatchInvalid("ungueltige Position %r" % pos)
        unit = self.find(info, target)
        if unit.kind == "header":
            if kind == "after":
                return unit.end, "", True
            raise PatchInvalid("Position %r bei @header nicht moeglich" % pos)
        if kind == "after":
            return unit.end, unit.indent, unit.parent is None
        if kind == "before":
            return unit.start, unit.indent, unit.parent is None
        if unit.kind != "class":
            raise PatchInvalid("%s verlangt eine Klasse, %r ist keine" % (kind, target))
        first = unit.node.body[0]
        if first.lineno == unit.node.lineno:
            raise PatchInvalid("Klasse %r ist einzeilig - %s nicht moeglich" % (target, kind))
        body_indent = leading_ws(lines[first.lineno - 1])
        if kind == "end-of":
            return unit.end, body_indent, False
        if is_docstring(first):
            return first.end_lineno, body_indent, False
        for sub in info.units:
            if sub.node is first:
                return sub.start, body_indent, False
        return first.lineno - 1, body_indent, False

    def detect_unit(self, doc, info=None):
        """Ebenen-Einheit aus den Bloecken der Datei (haeufigste Differenz)."""
        info = info or self.analyze(doc)
        lines = info.lines
        diffs = Counter()
        for node in ast.walk(info.tree):
            if isinstance(node, ast.Module):
                continue
            for field in ("body", "orelse", "finalbody"):
                block = getattr(node, field, None)
                if not isinstance(block, list) or not block:
                    continue
                child = block[0]
                if not hasattr(child, "lineno") or child.lineno <= node.lineno:
                    continue
                outer = leading_ws(lines[node.lineno - 1])
                inner = leading_ws(lines[child.lineno - 1])
                if inner.startswith(outer) and len(inner) > len(outer):
                    diffs[inner[len(outer):]] += 1
        if diffs:
            return diffs.most_common(1)[0][0]
        return detect_generic(lines)

    # -- Normalform --------------------------------------------------------

    def _skip(self, tree, prof):
        if prof.get("docstrings") != "ignore":
            return set()
        skip = set()
        for node in ast.walk(tree):
            if isinstance(node, DOC_OWNERS) and node.body and is_docstring(node.body[0]):
                skip.add(id(node.body[0]))
        return skip

    @staticmethod
    def _exact(prof):
        return prof["comments"] == "keep" or prof["whitespace"] == "keep"

    def normalize(self, doc, prof):
        if self._exact(prof):
            return doc.text()
        tree = self.parse(doc)
        skip = self._skip(tree, prof)
        return "\n".join((_head_lines(doc.lines), _ser(tree, skip),
                          _indent_sig(ast.walk(tree), doc.lines, skip)))

    def normalize_unit(self, doc, info, unit, prof):
        lines = info.lines
        if self._exact(prof):
            return "\n".join(lines[unit.start:unit.end])
        skip = self._skip(info.tree, prof)
        if unit.kind == "header":
            parts = [_head_lines(lines)]
            if unit.node is not None and id(unit.node) not in skip:
                parts.append(_ser(unit.node, skip))
            return "\n".join(parts)
        return _ser(unit.node, skip) + "\n" + _indent_sig(ast.walk(unit.node), lines, skip)
