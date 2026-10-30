"""mode css / scss - Stylesheets (manifest.txt Abschnitte 5.6.2, 7.2).

Normalform css/1: Regeln und Deklarationen in ihrer Reihenfolge; Kommentare
und unwichtiger Leerraum zaehlen nicht. Leerraum in Selektoren bleibt
(a :hover ist nicht a:hover). Einheiten: Regeln und At-Regeln mit Block,
verschachtelt; Adresse ueber den Selektor, Ebenen getrennt durch "/".
scss (auch fuer LESS): zusaetzlich //-Kommentare und #{...}-Interpolation.
"""

import bisect
import re
from collections import Counter

from ..errors import PatchInvalid
from ..layout import leading_ws, detect_generic
from .base import Adapter, Info, ParseError, Unit

SPACE = " \t\r\n\f"
SINGLE = "{}();:,[]"


class Token(object):
    __slots__ = ("kind", "text", "start", "end", "line", "ws")

    def __init__(self, kind, text, start, end, ws):
        self.kind = kind      # comment, str, url, word, punct
        self.text = text
        self.start = start
        self.end = end
        self.line = 0
        self.ws = ws          # Leerraum direkt davor


def _scan_string(src, i):
    quote, j, n = src[i], i + 1, len(src)
    while j < n:
        if src[j] == "\\":
            j += 2
            continue
        if src[j] == quote:
            return j + 1
        if src[j] == "\n":
            break
        j += 1
    raise ParseError("Zeichenkette ohne Ende (Position %d)" % i)


def _scan_interpolation(src, i):
    """#{ ... } (scss), verschachtelt; i zeigt auf '#'."""
    depth, j, n = 0, i + 1, len(src)
    while j < n:
        ch = src[j]
        if ch in "'\"":
            j = _scan_string(src, j)
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return j + 1
        j += 1
    raise ParseError("Interpolation #{ ohne Ende (Position %d)" % i)


def tokenize(src, scss=False):
    tokens, i, n, ws = [], 0, len(src), False
    while i < n:
        c = src[i]
        if c in SPACE:
            ws = True
            i += 1
            continue
        start = i
        if src.startswith("/*", i):
            j = src.find("*/", i + 2)
            if j < 0:
                raise ParseError("Kommentar ohne Ende (Position %d)" % i)
            i, kind = j + 2, "comment"
        elif scss and src.startswith("//", i):
            j = src.find("\n", i)
            i, kind = (n if j < 0 else j), "comment"
        elif c in "'\"":
            i, kind = _scan_string(src, i), "str"
        elif c in SINGLE:
            i, kind = i + 1, "punct"
        elif scss and src.startswith("#{", i):
            i = _scan_interpolation(src, i)
            kind = "word"
            while i < n and src[i] not in SPACE + SINGLE + "'\"" and not src.startswith("/*", i):
                i = _scan_interpolation(src, i) if src.startswith("#{", i) else i + 1
        else:
            j = i
            while j < n and src[j] not in SPACE + SINGLE + "'\"" and not src.startswith("/*", j) \
                    and not (scss and (src.startswith("//", j) or src.startswith("#{", j))):
                j += 1
            if scss and src.startswith("#{", j):
                while j < n and src[j] not in SPACE + SINGLE + "'\"":
                    j = _scan_interpolation(src, j) if src.startswith("#{", j) else j + 1
            i, kind = j, "word"
            if src[start:i].lower() == "url" and i < n and src[i] == "(":
                k = i + 1
                while k < n and src[k] in SPACE:
                    k += 1
                if k < n and src[k] not in "'\"":
                    end = src.find(")", k)
                    if end < 0:
                        raise ParseError("url( ohne Ende (Position %d)" % start)
                    i, kind = end + 1, "url"
        tokens.append(Token(kind, src[start:i], start, i, ws))
        ws = False
    return tokens


class Node(object):
    """Regel/At-Regel mit Block (kind rule) oder Deklaration/Anweisung (decl)."""

    def __init__(self, kind, prelude, first, last, open_tok=None, children=None):
        self.kind = kind
        self.prelude = prelude        # Tokens (ohne Kommentare) vor "{" bzw. der Deklaration
        self.first = first            # erstes Token (inkl. Kommentare? nein: erstes Code-Token)
        self.last = last              # "}" bzw. ";" oder letztes Token
        self.open = open_tok
        self.children = children or []


def parse(tokens):
    code = [t for t in tokens if t.kind != "comment"]
    nodes, pos = _block(code, 0, top=True)
    if pos != len(code):
        raise ParseError("ueberzaehlige } (Position %d)" % code[pos].start)
    return nodes


def _block(code, i, top=False):
    nodes, current, depth = [], [], 0
    while i < len(code):
        tok = code[i]
        if tok.kind == "punct":
            if tok.text in "([":
                depth += 1
            elif tok.text in ")]":
                depth -= 1
            elif depth == 0 and tok.text == ";":
                if current:
                    nodes.append(Node("decl", current, current[0], tok))
                current = []
                i += 1
                continue
            elif depth == 0 and tok.text == "{":
                if not current:
                    raise ParseError("Block ohne Selektor (Position %d)" % tok.start)
                children, j = _block(code, i + 1)
                if j >= len(code):
                    raise ParseError("{ nicht geschlossen (Position %d)" % tok.start)
                nodes.append(Node("rule", current, current[0], code[j], tok, children))
                current = []
                i = j + 1
                continue
            elif depth == 0 and tok.text == "}":
                if top:
                    return nodes, i
                if current:
                    nodes.append(Node("decl", current, current[0], current[-1]))
                return nodes, i
        current.append(tok)
        i += 1
    if not top:
        return nodes, i
    if current:
        nodes.append(Node("decl", current, current[0], current[-1]))
    return nodes, i


def _join(tokens, colon=False):
    """Tokens mit vereinheitlichtem Leerraum; keiner neben ',' (und ':' bei colon)."""
    out, prev = [], None
    for tok in tokens:
        tight = prev is None or tok.text == "," or prev.text == "," or \
            (colon and (tok.text == ":" or prev.text == ":"))
        if tok.ws and not tight:
            out.append(" ")
        out.append(tok.text)
        prev = tok
    return "".join(out)


def prelude_text(tokens):
    return _join(tokens)


def _decl_text(tokens):
    colon = next((k for k, t in enumerate(tokens) if t.text == ":"), None)
    if colon is None:
        return _join(tokens)
    return _join(tokens[:colon]) + ":" + _join(tokens[colon + 1:])


def normal_nodes(nodes):
    parts = []
    for node in nodes:
        if node.kind == "rule":
            parts.append(prelude_text(node.prelude) + "{" + normal_nodes(node.children) + "}")
        else:
            parts.append(_decl_text(node.prelude) + ";")
    return "".join(parts)


class Sheet(object):
    def __init__(self, lines, scss):
        self.lines = lines
        self.text = "\n".join(lines) + "\n"
        self.tokens = tokenize(self.text, scss)
        self.nodes = parse(self.tokens)
        starts, pos = [], 0
        for line in lines:
            starts.append(pos)
            pos += len(line) + 1
        self.starts = starts
        for tok in self.tokens:
            tok.line = bisect.bisect_right(starts, tok.start) - 1
        code_lines = set(t.line for t in self.tokens if t.kind != "comment")
        self.comment_lines = set()
        for tok in self.tokens:
            if tok.kind == "comment":
                last = bisect.bisect_right(starts, tok.end - 1) - 1
                self.comment_lines.update(l for l in range(tok.line, last + 1)
                                          if l not in code_lines)
        self.order = dict((id(t), k) for k, t in enumerate(self.tokens))

    def alone_before(self, tok):
        return not self.lines[tok.line][:tok.start - self.starts[tok.line]].strip()

    def alone_after(self, tok):
        k = self.order[id(tok)] + 1
        while k < len(self.tokens) and self.tokens[k].line == tok.line:
            if self.tokens[k].kind != "comment":
                return False
            k += 1
        return True


class CssAdapter(Adapter):
    mode = "css"
    label = "CSS"
    has_units = True
    default_indent = "  "
    gap_top = 1
    gap_nested = 0
    dup_re = re.compile(r"\{\d+\}$")
    scss = False

    def _sheet(self, doc):
        return Sheet(doc.lines, self.scss)

    def normalize(self, doc, prof):
        if prof["whitespace"] == "keep":
            return doc.text()
        if prof["comments"] == "keep":
            sheet = self._sheet(doc)
            return "\n".join(t.text for t in sheet.tokens if t.kind == "comment") + "\n" + \
                normal_nodes(sheet.nodes)
        return normal_nodes(self._sheet(doc).nodes)

    def normalize_unit(self, doc, info, unit, prof):
        if prof["whitespace"] == "keep":
            return "\n".join(info.lines[unit.start:unit.end])
        text = normal_nodes([unit.node])
        if prof["comments"] == "keep":
            comments = [t.text for t in info.sheet.tokens
                        if t.kind == "comment" and unit.start <= t.line < unit.end]
            text = "\n".join(comments) + "\n" + text
        return text

    def validate(self, doc):
        self._sheet(doc)

    def opens_block(self, line):
        return line.rstrip().endswith("{")

    def closes_block(self, line):
        return line.lstrip().startswith("}")

    def can_contain(self, unit):
        return True

    def rename(self, rel_lines, name):
        for i, line in enumerate(rel_lines):
            stripped = line.strip()
            if not stripped or stripped.startswith(("/*", "*", "//")):
                continue
            if stripped.endswith("{"):
                lead = line[:len(line) - len(line.lstrip("\t"))]
                return rel_lines[:i] + [lead + name + " {"] + rel_lines[i + 1:]
            break
        raise PatchInvalid("Selektor muss fuer as= in einer Zeile mit '{' stehen")

    def detect_unit(self, doc, info=None):
        try:
            info = info or self.analyze(doc)
        except ParseError:
            return detect_generic(doc.lines)
        diffs = Counter()
        for unit in info.units:
            node = unit.node
            inner = next((c.first for c in node.children), None)
            if inner is not None and inner.line > node.open.line:
                outer = leading_ws(info.lines[node.open.line])
                ws = leading_ws(info.lines[inner.line])
                if ws.startswith(outer) and len(ws) > len(outer):
                    diffs[ws[len(outer):]] += 1
        return diffs.most_common(1)[0][0] if diffs else detect_generic(doc.lines)

    def analyze(self, doc):
        sheet = self._sheet(doc)
        units = []
        self._collect(sheet, sheet.nodes, None, "", units)
        return Info(doc.lines, units, sheet=sheet, start_line=self._start_line(sheet))

    def _start_line(self, sheet):
        line = 0
        while line < len(sheet.lines) and (line in sheet.comment_lines or
                                           not sheet.lines[line].strip()):
            line += 1
        for node in sheet.nodes:
            if node.kind == "decl" and node.first.text.lower() in (
                    "@charset", "@import", "@use", "@forward", "@namespace"):
                line = node.last.line + 1
            else:
                break
        return line

    def _collect(self, sheet, nodes, parent, prefix, units):
        rules = [n for n in nodes if n.kind == "rule"
                 and sheet.alone_before(n.first) and sheet.alone_after(n.last)]
        labels = self._labels([prelude_text(n.prelude) for n in rules])
        for label, node in zip(labels, rules):
            start = node.first.line
            floor = parent.node.open.line + 1 if parent is not None else 0
            while start - 1 >= floor and start - 1 in sheet.comment_lines:
                start -= 1
            path = prefix + "/" + label if prefix else label
            kind = "at-rule" if node.first.text.startswith("@") else "rule"
            unit = Unit(path, kind, node, start, node.last.line + 1,
                        leading_ws(sheet.lines[node.first.line]), parent)
            units.append(unit)
            self._collect(sheet, node.children, unit, path, units)

    @staticmethod
    def _labels(names):
        counts = Counter(names)
        seen, out = Counter(), []
        for name in names:
            seen[name] += 1
            out.append(name if counts[name] == 1 else "%s{%d}" % (name, seen[name]))
        return out

    def _body(self, unit, info):
        node, sheet = unit.node, info.sheet
        if not sheet.alone_after(node.open) or not sheet.alone_before(node.last):
            return None
        if node.children:
            indent = leading_ws(info.lines[node.children[0].first.line])
        else:
            indent = unit.indent + (self.detect_unit(None, info) or self.default_indent)
        return node.open.line + 1, node.last.line, indent

    def position(self, info, pos):
        return self.simple_position(info, pos, lambda unit: self._body(unit, info))


class ScssAdapter(CssAdapter):
    mode = "scss"
    label = "SCSS"
    scss = True
