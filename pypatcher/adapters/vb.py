"""mode vb (VB.NET) und vba (VBA/VB6/VBScript) - Visual Basic (R033,
manifest.txt Abschnitte 5.6.8, 7.2).

Normalform vb/1: logische Zeilen (Fortsetzung " _" aufgeloest, ":" trennt
Anweisungen) aus Tokens; Kommentare (' und REM) zaehlen nicht, Schluessel-
woerter und Namen ohne Gross-/Kleinschreibung (VB unterscheidet sie nicht),
Zeichenketten exakt. Einheiten: Sub, Function, Property, Operator, Custom
Event (mit End ...), Class, Module, Structure, Interface, Namespace, Enum,
Type. Deklarationen ohne Rumpf (Declare, Delegate, MustOverride, Interface-
Mitglieder, automatische Properties) sind keine Einheiten.
"""

import bisect
import re
from collections import Counter

from ..errors import Conflict, PatchInvalid
from ..layout import leading_ws, detect_generic
from .base import Adapter, Info, ParseError, Unit

PUNCT = sorted(("<= >= <> := += -= *= /= \\= ^= &= <<= >>= << >> ?. . , ( ) { } = < > + - * / "
                "\\ ^ & : ! ? ;").split(), key=len, reverse=True)
NUMBER_RE = re.compile(r"&[HhOoBb][0-9A-Fa-f_]+[%&SILUsilu]*|"
                       r"(?:\d[\d_]*\.?[\d_]*|\.\d[\d_]*)(?:[eE][+-]?\d+)?[%&@!#$FRDSILUCfrdsiluc]*")
SPACE = " \t\r\f\v"
MODIFIERS = {"public", "private", "friend", "protected", "shared", "static", "overrides",
             "overridable", "notoverridable", "notinheritable", "mustinherit", "partial",
             "shadows", "overloads", "readonly", "writeonly", "default", "async", "iterator",
             "widening", "narrowing", "global", "custom"}
MEMBERS = ("sub", "function", "property", "operator", "event")
CONTAINERS = ("class", "module", "structure", "interface", "namespace")
PLAIN_BLOCKS = ("enum", "type")
HEADER_WORDS = ("VERSION ", "ATTRIBUTE ", "OBJECT ", "BEGIN ", "BEGIN\t")
OPENER_RE = re.compile(r"^\s*(?:<[^>]*>\s*)?(?:(?:%s)\s+)*(?:sub|function|property|operator|"
                       r"class|module|structure|interface|namespace|enum|type|get|set)\b"
                       % "|".join(sorted(MODIFIERS)), re.I)
RENAME_RE = re.compile(r"^(\s*(?:<[^>]*>\s*)?(?:\w+\s+)*?(?:sub|function|operator|class|module|"
                       r"structure|interface|enum|type|property(?:\s+(?:get|let|set))?)\s+)"
                       r"(\w+)", re.I)


class Token(object):
    __slots__ = ("kind", "text", "start", "end", "line")

    def __init__(self, kind, text, start, end):
        self.kind = kind      # nl, comment, pp, str, date, num, name, punct
        self.text = text
        self.start = start
        self.end = end
        self.line = 0


def tokenize(src):
    tokens, i, n = [], 0, len(src)
    line_start = True
    while i < n:
        c = src[i]
        if c == "\n":
            tokens.append(Token("nl", c, i, i + 1))
            i += 1
            line_start = True
            continue
        if c in SPACE:
            i += 1
            continue
        start = i
        # Zeilenfortsetzung: " _" am Zeilenende
        if c == "_" and (i == 0 or src[i - 1] in SPACE) and \
                re.match(r"_[ \t\r]*(?:\n|$)", src[i:i + 40]):
            j = src.find("\n", i)
            i = n if j < 0 else j + 1
            continue
        if c == "#" and line_start:                  # #If, #Region, #Const ...
            j = src.find("\n", i)
            i = n if j < 0 else j
            tokens.append(Token("pp", src[start:i], start, i))
            continue
        word_rem = re.match(r"rem(?=\s|$)", src[i:i + 4], re.I) and \
            (not tokens or tokens[-1].kind == "nl" or tokens[-1].text == ":")
        if c in "'‘’" or word_rem:
            j = src.find("\n", i)
            i = n if j < 0 else j
            tokens.append(Token("comment", src[start:i], start, i))
            continue
        line_start = False
        if c == '"' or (c == "$" and src.startswith('$"', i)):
            j = i + (2 if c == "$" else 1)
            while True:
                if j >= n or src[j] == "\n":
                    raise ParseError("Zeichenkette ohne Ende (Position %d)" % i)
                if src[j] == '"':
                    if src.startswith('""', j):
                        j += 2
                        continue
                    break
                j += 1
            i = j + 1
            if i < n and src[i] in "cC" and (i + 1 >= n or not src[i + 1].isalnum()):
                i += 1                                   # "x"c (Char)
            tokens.append(Token("str", src[start:i], start, i))
            continue
        if c == "#":                                     # Datumsliteral #1/2/2020#
            j = src.find("#", i + 1)
            if j < 0 or "\n" in src[i:j]:
                raise ParseError("Datumsliteral ohne Ende (Position %d)" % i)
            i = j + 1
            tokens.append(Token("date", src[start:i], start, i))
            continue
        if c.isdigit() or (c == "&" and i + 1 < n and src[i + 1] in "HhOoBb" and
                           i + 2 < n and src[i + 2].isalnum()) or \
                (c == "." and i + 1 < n and src[i + 1].isdigit()):
            i = NUMBER_RE.match(src, i).end() or i + 1
            tokens.append(Token("num", src[start:i], start, i))
            continue
        if c == "[":                                      # [Name] (geschuetzter Bezeichner)
            j = src.find("]", i)
            if j > i and re.match(r"^\[\w+\]$", src[i:j + 1]):
                i = j + 1
                tokens.append(Token("name", src[start:i], start, i))
                continue
        if c.isalpha() or c == "_" or ord(c) > 127:
            j = i + 1
            while j < n and (src[j].isalnum() or src[j] == "_" or ord(src[j]) > 127):
                j += 1
            if j < n and src[j] in "$%&!#@" and (j + 1 >= n or not src[j + 1].isalnum()):
                j += 1                                   # Typzeichen: Left$, Count%
            i = j
            tokens.append(Token("name", src[start:i], start, i))
            continue
        for p in PUNCT:
            if src.startswith(p, i):
                break
        else:
            raise ParseError("unerwartetes Zeichen %r (Position %d)" % (c, i))
        i += len(p)
        tokens.append(Token("punct", p, start, i))
    return tokens


def _norm_text(tok):
    return tok.text if tok.kind in ("str", "date", "comment", "pp") else tok.text.lower()


def normal_tokens(tokens, keep_comments=False):
    """Logische Zeilen: Tokens mit Leerzeichen, Zeilen mit Zeilenumbruch."""
    lines, cur = [], []
    for tok in tokens:
        if tok.kind == "nl" or (tok.kind == "punct" and tok.text == ":"):
            if cur:
                lines.append(" ".join(cur))
                cur = []
            continue
        if tok.kind == "comment" and not keep_comments:
            continue
        if tok.kind == "pp":
            text = re.sub(r"'.*$", "", tok.text) if not keep_comments else tok.text
            cur.append(" ".join(text.lower().split()))
            continue
        cur.append(_norm_text(tok))
    if cur:
        lines.append(" ".join(cur))
    return "\n".join(lines)


class Statement(object):
    __slots__ = ("first", "last", "words", "texts", "line", "end_line")

    def __init__(self, toks, first, last):
        self.first = first                       # Index des ersten/letzten Code-Tokens
        self.last = last
        self.texts = [t.text for t in toks]      # wie im Code (fuer Namen)
        self.words = [t.lower() for t in self.texts]
        self.line = toks[0].line
        self.end_line = toks[-1].line


class Source(object):
    def __init__(self, lines):
        self.lines = lines
        self.text = "\n".join(lines) + "\n"
        self.tokens = tokenize(self.text)
        starts, pos = [], 0
        for line in lines:
            starts.append(pos)
            pos += len(line) + 1
        self.starts = starts
        for tok in self.tokens:
            tok.line = bisect.bisect_right(starts, tok.start) - 1
        self.statements, cur = [], []
        for k, tok in enumerate(self.tokens):
            if tok.kind == "nl" or (tok.kind == "punct" and tok.text == ":") or \
                    tok.kind in ("comment", "pp"):
                if cur:
                    self.statements.append(Statement([self.tokens[i] for i in cur], cur[0], cur[-1]))
                    cur = []
                continue
            cur.append(k)
        if cur:
            self.statements.append(Statement([self.tokens[i] for i in cur], cur[0], cur[-1]))
        code = set(t.line for t in self.tokens if t.kind not in ("nl", "comment"))
        self.comment_lines = set(t.line for t in self.tokens if t.kind == "comment"
                                 and t.line not in code)


def _skip_attributes(words):
    """<Attribut(...)> am Anfang einer Anweisung ueberspringen -> Index."""
    k = 0
    while k < len(words) and words[k] == "<":
        depth = 0
        while k < len(words):
            if words[k] == "<":
                depth += 1
            elif words[k] == ">":
                depth -= 1
                if depth == 0:
                    break
            k += 1
        k += 1
    return k


def _opener(stmt):
    """(art, name) einer Anweisung, die einen Block beginnen kann, sonst None."""
    skip = _skip_attributes(stmt.words)
    words, texts = stmt.words[skip:], stmt.texts[skip:]
    if not words or words[0] in ("end", "exit", "declare", "delegate"):
        return None
    if "declare" in words[:4] or "delegate" in words[:4] or "mustoverride" in words[:4]:
        return None
    k = 0
    while k < len(words) and words[k] in MODIFIERS:
        k += 1
    if k >= len(words):
        return None
    kind = words[k]
    if kind == "event" and "custom" not in words[:k]:
        return None
    if kind not in MEMBERS + CONTAINERS + PLAIN_BLOCKS:
        return None
    rest = texts[k + 1:]
    if kind == "property" and rest and rest[0].lower() in ("get", "let", "set"):
        rest = rest[1:]
    if kind == "operator":
        return (kind, "operator" + rest[0]) if rest else None
    if not rest:
        return None
    if kind == "namespace":
        name = []
        for w in rest:
            if w == "(":
                break
            name.append(w)
        return kind, "".join(name)
    return kind, rest[0].strip("[]")


def _closer(stmt):
    words = stmt.words
    if len(words) >= 2 and words[0] == "end" and words[1] in MEMBERS + CONTAINERS + PLAIN_BLOCKS:
        return words[1]
    return None


class VbAdapter(Adapter):
    mode = "vb"
    label = "Visual Basic"
    has_units = True
    default_indent = "    "
    gap_top = 1
    gap_nested = 1
    vba = False

    # -- Normalform ------------------------------------------------------

    def normalize(self, doc, prof):
        if prof["whitespace"] == "keep":
            return doc.text()
        return normal_tokens(tokenize("\n".join(doc.lines) + "\n"), prof["comments"] == "keep")

    def normalize_unit(self, doc, info, unit, prof):
        if prof["whitespace"] == "keep":
            return "\n".join(info.lines[unit.start:unit.end])
        toks = [t for t in info.src.tokens if unit.start <= t.line < unit.end]
        return normal_tokens(toks, prof["comments"] == "keep")

    def validate(self, doc):
        self.analyze(doc)

    def opens_block(self, line):
        return bool(OPENER_RE.match(line)) and not re.match(r"^\s*(?:\w+\s+)*declare\b", line, re.I)

    def closes_block(self, line):
        return bool(re.match(r"^\s*end\s+\w+", line, re.I))

    def can_contain(self, unit):
        return unit.kind in CONTAINERS

    def rename(self, rel_lines, name):
        if not re.match(r"^[A-Za-z_]\w*$", name or ""):
            raise PatchInvalid("as=%r ist kein gueltiger Name" % name)
        for i, line in enumerate(rel_lines):
            stripped = line.strip()
            if not stripped or stripped.startswith("'") or stripped.lower().startswith("rem ") \
                    or (stripped.startswith("<") and stripped.endswith(">")):
                continue
            match = RENAME_RE.match(line)
            if match:
                return rel_lines[:i] + [match.group(1) + name + line[match.end(2):]] + \
                    rel_lines[i + 1:]
            break
        raise PatchInvalid("keine Sub-/Function-/Class-Zeile zum Umbenennen gefunden")

    def find(self, info, path):
        """Wie im Code - VB unterscheidet Gross-/Kleinschreibung nicht, daher
        notfalls ohne (wenn eindeutig)."""
        try:
            return Adapter.find(self, info, path)
        except Conflict:
            same = [u for u in info.units if u.path.lower() == path.lower()]
            if len(same) == 1:
                return same[0]
            raise

    def detect_unit(self, doc, info=None):
        try:
            info = info or self.analyze(doc)
        except ParseError:
            return detect_generic(doc.lines)
        diffs = Counter()
        for unit in info.units:
            body = self._body(unit, info)
            if body is None or body[0] >= body[1]:
                continue
            ws = next((leading_ws(l) for l in info.lines[body[0]:body[1]] if l.strip()), None)
            if ws is not None and ws.startswith(unit.indent) and len(ws) > len(unit.indent):
                diffs[ws[len(unit.indent):]] += 1
        return diffs.most_common(1)[0][0] if diffs else detect_generic(doc.lines)

    # -- Einheiten -------------------------------------------------------

    def _header_end(self, lines):
        """VBA-/VB6-Export: VERSION, BEGIN ... END, Attribute, Object -> @header."""
        i, depth = 0, 0
        while i < len(lines):
            text = lines[i].strip()
            upper = text.upper()
            if depth:
                if upper.startswith("BEGIN"):
                    depth += 1
                elif upper == "END":
                    depth -= 1
                i += 1
                continue
            if upper.startswith(HEADER_WORDS) or upper == "BEGIN":
                if upper.startswith("BEGIN"):
                    depth = 1
                i += 1
                continue
            break
        return i

    def analyze(self, doc):
        src = Source(doc.lines)
        header_end = self._header_end(doc.lines)
        units = [Unit("@header", "header", None, 0, header_end, "", None)]
        stack, found = [], []                    # offene Bloecke: [art, name, stmt, parent-index]
        stmts = [s for s in src.statements if s.line >= header_end]
        for idx, stmt in enumerate(stmts):
            closer = _closer(stmt)
            if closer is not None:
                if not stack or stack[-1][0] != closer:
                    if stack and stack[-1][0] in MEMBERS and closer in CONTAINERS:
                        stack.pop()               # Mitglied ohne End (Deklaration) davor
                    if not stack or stack[-1][0] != closer:
                        raise ParseError("Zeile %d: 'End %s' ohne passenden Anfang"
                                         % (stmt.line + 1, closer.capitalize()))
                kind, name, open_stmt, parent = stack.pop()
                found.append((kind, name, open_stmt, stmt, parent))
                continue
            hit = _opener(stmt)
            if hit is None:
                continue
            kind, name = hit
            if stack and stack[-1][0] in MEMBERS:
                # neues Mitglied, waehrend ein Mitglied offen ist: das offene war
                # eine Deklaration ohne Rumpf (z.B. Auto-Property) - verwerfen
                if kind in MEMBERS or kind in CONTAINERS:
                    stack.pop()
            if stack and stack[-1][0] == "interface" and kind in MEMBERS:
                continue                          # Interface-Mitglieder haben keinen Rumpf
            stack.append([kind, name, stmt, len(found)])
        while stack and stack[-1][0] in MEMBERS:
            stack.pop()
        if stack:
            raise ParseError("Zeile %d: %s %s ohne End %s" % (
                stack[-1][2].line + 1, stack[-1][0].capitalize(), stack[-1][1],
                stack[-1][0].capitalize()))
        found.sort(key=lambda f: f[2].line)
        self._build(src, found, header_end, units)
        return Info(doc.lines, units, src=src, header_end=header_end)

    def _build(self, src, found, header_end, units):
        lines = src.lines
        spans = [(f[2].line, f[3].end_line + 1) for f in found]
        parents = []
        for i, (start, end) in enumerate(spans):
            parent = None
            for j in range(i - 1, -1, -1):
                if spans[j][0] <= start and end <= spans[j][1]:
                    parent = j
                    break
            parents.append(parent)
        built = {}
        siblings = {}
        for i, f in enumerate(found):
            siblings.setdefault(parents[i], []).append(i)
        labels = {}
        for parent, members in siblings.items():
            for i, label in zip(members, self.label_dups([found[i][1] for i in members])):
                labels[i] = label
        for i, (kind, name, open_stmt, close_stmt, _) in enumerate(found):
            parent = built.get(parents[i])
            path = parent.path + "." + labels[i] if parent is not None else labels[i]
            start = open_stmt.line
            floor = (found[parents[i]][2].end_line + 1) if parents[i] is not None else header_end
            while start - 1 >= floor and (start - 1 in src.comment_lines or
                                          re.match(r"^\s*<.*>\s*_?\s*$", lines[start - 1])):
                start -= 1
            unit = Unit(path, kind, (open_stmt, close_stmt), start, close_stmt.end_line + 1,
                        leading_ws(lines[open_stmt.line]), parent)
            built[i] = unit
            units.append(unit)

    def _body(self, unit, info):
        if unit.node is None:
            return None
        open_stmt, close_stmt = unit.node
        first, last = open_stmt.end_line + 1, close_stmt.line
        if first > last:
            return None
        ws = next((leading_ws(l) for l in info.lines[first:last] if l.strip()), None)
        indent = ws if ws is not None and len(ws) > len(unit.indent) else \
            unit.indent + self.default_indent
        return first, last, indent

    def position(self, info, pos):
        if pos == "start":
            return info.header_end, "", True
        if pos.endswith(":@header") and pos.startswith("after:"):
            return info.header_end, "", True
        return self.simple_position(info, pos, lambda unit: self._body(unit, info))


class VbaAdapter(VbAdapter):
    """VBA/VB6/VBScript: Module (*.bas), Klassen (*.cls), Formulare (*.frm),
    *.vbs - Datei-Kopf des Exports (VERSION, BEGIN ... END, Attribute) ist
    @header; Property Get/Let/Set gleichen Namens: name#1, name#2 ..."""
    mode = "vba"
    label = "VBA"
    vba = True
