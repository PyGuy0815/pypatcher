"""mode lua - Lua (R034, manifest.txt Abschnitte 5.6.7, 7.2).

Normalform lua/1: Tokenfolge ohne Kommentare und Leerraum; Zeichenketten
(auch lange [[...]] / [==[...]==]) bleiben exakt. Einheiten: function name,
local function name, name = function, function M.name / M:methode, Tabellen
in local/globalen Zuweisungen (local M = {...}) mit ihren Funktionen
(M.name = function ... end), innere Funktionen. Bloecke werden ueber
function/if/do/while/for/repeat ... end/until gezaehlt.
"""

import bisect
import re
from collections import Counter

from ..errors import PatchInvalid
from ..layout import leading_ws, detect_generic
from .base import Adapter, Info, ParseError, Unit

PUNCT = sorted(("+ - * / % ^ # & ~ | << >> // == ~= <= >= < > = ( ) { } [ ] :: ; : , . .. "
                "...").split(), key=len, reverse=True)
NUMBER_RE = re.compile(r"0[xX][0-9a-fA-F]*(?:\.[0-9a-fA-F]*)?(?:[pP][+-]?\d+)?|"
                       r"(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?")
LONG_RE = re.compile(r"\[(=*)\[")
CLOSE = {"(": ")", "[": "]", "{": "}"}
OPENERS = ("function", "if", "do", "repeat", "while", "for")
SPACE = " \t\r\f\v"


class Token(object):
    __slots__ = ("kind", "text", "start", "end", "line")

    def __init__(self, kind, text, start, end):
        self.kind = kind      # nl, comment, str, num, name, punct
        self.text = text
        self.start = start
        self.end = end
        self.line = 0


def _long_end(src, i, level):
    end = src.find("]" + "=" * level + "]", i)
    if end < 0:
        raise ParseError("lange Zeichenkette/Kommentar ohne Ende (Position %d)" % i)
    return end + level + 2


def tokenize(src):
    tokens, i, n = [], 0, len(src)
    if src.startswith("#!"):
        j = src.find("\n")
        j = n if j < 0 else j
        tokens.append(Token("comment", src[:j], 0, j))
        i = j
    while i < n:
        c = src[i]
        if c == "\n":
            tokens.append(Token("nl", c, i, i + 1))
            i += 1
            continue
        if c in SPACE:
            i += 1
            continue
        start = i
        if src.startswith("--", i):
            long = LONG_RE.match(src, i + 2)
            if long:
                i = _long_end(src, long.end(), len(long.group(1)))
            else:
                j = src.find("\n", i)
                i = n if j < 0 else j
            tokens.append(Token("comment", src[start:i], start, i))
            continue
        long = LONG_RE.match(src, i)
        if long:
            i = _long_end(src, long.end(), len(long.group(1)))
            tokens.append(Token("str", src[start:i], start, i))
            continue
        if c in "'\"":
            j = i + 1
            while j < n and src[j] != c:
                if src[j] == "\\":
                    if src.startswith("\\z", j):          # \z ueberspringt Leerraum
                        j += 2
                        while j < n and src[j] in SPACE + "\n":
                            j += 1
                        continue
                    j += 2
                    continue
                if src[j] == "\n":
                    raise ParseError("Zeichenkette ohne Ende (Position %d)" % i)
                j += 1
            if j >= n:
                raise ParseError("Zeichenkette ohne Ende (Position %d)" % i)
            i = j + 1
            tokens.append(Token("str", src[start:i], start, i))
            continue
        if c.isdigit() or (c == "." and i + 1 < n and src[i + 1].isdigit()):
            i = NUMBER_RE.match(src, i).end()
            tokens.append(Token("num", src[start:i], start, i))
            continue
        if c.isalpha() or c == "_" or ord(c) > 127:
            j = i + 1
            while j < n and (src[j].isalnum() or src[j] == "_" or ord(src[j]) > 127):
                j += 1
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


def normal_tokens(tokens, keep_comments=False):
    out = []
    for tok in tokens:
        if tok.kind == "nl" or (tok.kind == "comment" and not keep_comments):
            continue
        out.append(tok.text)
    return " ".join(out)


class Source(object):
    """Tokens, Zeilen, Klammerpaare und Blockpaare (function ... end)."""

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
        self.sig = [t for t in self.tokens if t.kind not in ("nl", "comment")]
        self.pair, self.block = {}, {}
        brackets, blocks = [], []
        for k, tok in enumerate(self.sig):
            text = tok.text
            if tok.kind == "punct":
                if text in CLOSE:
                    brackets.append(k)
                elif text in (")", "]", "}"):
                    if not brackets or CLOSE[self.sig[brackets[-1]].text] != text:
                        raise ParseError("Klammer %r passt nicht (Position %d)" % (text, tok.start))
                    self.pair[brackets.pop()] = k
                continue
            if tok.kind != "name" or (k > 0 and self.sig[k - 1].text in (".", ":")
                                      and self.sig[k - 1].kind == "punct"):
                continue
            if text in ("while", "for"):
                blocks.append([k, "loop"])               # wartet auf "do"
            elif text == "do":
                if blocks and blocks[-1][1] == "loop":
                    blocks[-1][1] = "do"
                else:
                    blocks.append([k, "do"])
            elif text in ("function", "if", "repeat"):
                blocks.append([k, text])
            elif text in ("end", "until"):
                if not blocks or (text == "until") != (blocks[-1][1] == "repeat"):
                    raise ParseError("%r ohne passenden Blockanfang (Position %d)"
                                     % (text, tok.start))
                self.block[blocks.pop()[0]] = k
        if brackets:
            raise ParseError("Klammer %r nicht geschlossen" % self.sig[brackets[-1]].text)
        if blocks:
            raise ParseError("%r ohne end (Position %d)"
                             % (self.sig[blocks[-1][0]].text, self.sig[blocks[-1][0]].start))
        code = set(t.line for t in self.sig)
        self.comment_lines = set()
        for tok in self.tokens:
            if tok.kind == "comment":
                last = bisect.bisect_right(starts, tok.end - 1) - 1
                self.comment_lines.update(l for l in range(tok.line, last + 1) if l not in code)

    def text_of(self, k):
        return self.sig[k].text if 0 <= k < len(self.sig) else None

    def line_start(self, k):
        tok = self.sig[k]
        return not self.lines[tok.line][:tok.start - self.starts[tok.line]].strip()

    def line_end(self, k):
        return k + 1 >= len(self.sig) or self.sig[k + 1].line > self.sig[k].line


class LuaAdapter(Adapter):
    mode = "lua"
    label = "Lua"
    has_units = True
    default_indent = "  "
    gap_top = 1
    gap_nested = 1

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
        Source(doc.lines)

    def opens_block(self, line):
        stripped = line.rstrip()
        return stripped.endswith(("{", "then", "do", "else", "(")) or \
            bool(re.search(r"\bfunction\b[^)]*\)\s*$", stripped))

    def closes_block(self, line):
        return bool(re.match(r"^\s*(?:(?:end|until|else|elseif)\b|\})", line))

    def can_contain(self, unit):
        return unit.kind == "table"

    def rename(self, rel_lines, name):
        if not re.match(r"^[A-Za-z_]\w*$", name or ""):
            raise PatchInvalid("as=%r ist kein gueltiger Name" % name)
        for i, line in enumerate(rel_lines):
            if not line.strip() or line.lstrip().startswith("--"):
                continue
            match = re.match(r"^(\s*(?:local\s+)?function\s+(?:[\w.]+[.:])?)(\w+)", line) or \
                re.match(r"^(\s*(?:local\s+)?(?:[\w.]+\.)?)(\w+)(?=\s*=)", line)
            if match:
                return rel_lines[:i] + [match.group(1) + name + line[match.end(2):]] + \
                    rel_lines[i + 1:]
            break
        raise PatchInvalid("keine Funktions-/Zuweisungszeile zum Umbenennen gefunden")

    def detect_unit(self, doc, info=None):
        try:
            info = info or self.analyze(doc)
        except ParseError:
            return detect_generic(doc.lines)
        src, diffs = info.src, Counter()
        for unit in info.units:
            first, open_, last = unit.node
            line = src.sig[first].line
            inner = next((t for t in src.sig[open_ + 1:last] if t.line > line), None)
            if inner is None:
                continue
            outer, ws = leading_ws(src.lines[line]), leading_ws(src.lines[inner.line])
            if ws.startswith(outer) and len(ws) > len(outer):
                diffs[ws[len(outer):]] += 1
        return diffs.most_common(1)[0][0] if diffs else detect_generic(doc.lines)

    # -- Einheiten -------------------------------------------------------

    def analyze(self, doc):
        src = Source(doc.lines)
        units = []
        self._scope(src, 0, len(src.sig), None, "", units)
        line = 0
        while line < len(src.lines) and (line in src.comment_lines or not src.lines[line].strip()):
            line += 1
        return Info(doc.lines, units, src=src, start_line=line)

    def _skip(self, src, k):
        if k in src.pair:
            return src.pair[k] + 1
        if k in src.block:
            return src.block[k] + 1
        return k + 1

    def _name_path(self, src, j, hi):
        """name(.name)*(:name)? ab j -> (text, index dahinter) oder (None, j)."""
        if j >= hi or src.sig[j].kind != "name":
            return None, j
        parts = [src.sig[j].text]
        j += 1
        while src.text_of(j) in (".", ":") and j + 1 < hi and src.sig[j + 1].kind == "name":
            parts.append(src.text_of(j) + src.sig[j + 1].text)
            if src.text_of(j) == ":":
                j += 2
                break
            j += 2
        return "".join(parts), j

    def _function_at(self, src, fn, hi):
        """fn zeigt auf "function"; liefert (Parameter-"(", end) oder None."""
        j = fn + 1
        while j < hi and src.text_of(j) != "(":
            j += 1
        if j >= hi or fn not in src.block or j not in src.pair:
            return None
        return j, src.block[fn]

    def _statement(self, src, k, hi, member):
        first, j = k, k
        if member:                                   # Tabellenfeld: name = ... / ["name"] = ...
            if src.sig[j].kind == "name" and src.text_of(j + 1) == "=" and \
                    src.text_of(j + 2) != "=":
                name, j = src.sig[j].text, j + 2
            elif src.text_of(j) == "[" and j + 1 < hi and src.sig[j + 1].kind == "str" and \
                    src.text_of(j + 2) == "]" and src.text_of(j + 3) == "=":
                name, j = src.sig[j + 1].text[1:-1], j + 4
            else:
                return None
        else:
            local = src.text_of(j) == "local"
            if local:
                j += 1
            if src.text_of(j) == "function":
                name, after = self._name_path(src, j + 1, hi)
                if name is None or (local and after != j + 2):
                    return None
                hit = self._function_at(src, j, hi)
                return None if hit is None else (name, "function", first, hit[0], hit[1])
            name, j = self._name_path(src, j, hi)
            if name is None or ":" in name or src.text_of(j) != "=":
                return None
            j += 1
        if src.text_of(j) == "function":
            hit = self._function_at(src, j, hi)
            return None if hit is None else (name, "function", first, hit[0], hit[1])
        if src.text_of(j) == "{" and j in src.pair:
            return (name, "table", first, j, src.pair[j])
        return None

    def _scope(self, src, lo, hi, parent, prefix, units):
        member = parent is not None and parent.kind == "table"
        found, k = [], lo
        while k < hi:
            fresh = src.line_start(k) and (k == lo or src.sig[k - 1].line < src.sig[k].line or
                                           src.text_of(k - 1) in (",", ";"))
            hit = self._statement(src, k, hi, member) if fresh else None
            if hit is None:
                k = self._skip(src, k)
                continue
            name, kind, first, open_, last = hit
            if src.text_of(last + 1) in ((",", ";") if member else (";",)) and \
                    src.sig[last + 1].line == src.sig[last].line:
                last += 1
            if src.line_end(last):
                found.append((name, kind, first, open_, last))
            k = last + 1
        names = self.label_dups([f[0] for f in found])
        for label, (name, kind, first, open_, last) in zip(names, found):
            start = src.sig[first].line
            floor = src.sig[parent.node[1]].line + 1 if parent is not None else 0
            while start - 1 >= floor and start - 1 in src.comment_lines:
                start -= 1
            if prefix:
                path = prefix + ("" if label.startswith(":") else ".") + label
            else:
                path = label
            unit = Unit(path, kind, (first, open_, last), start, src.sig[last].line + 1,
                        leading_ws(src.lines[src.sig[first].line]), parent)
            units.append(unit)
            if kind == "table":
                self._scope(src, open_ + 1, src.pair[open_], unit, path, units)
            else:                                     # innere Funktionen
                end = last if src.text_of(last) == "end" else last - 1
                self._scope(src, src.pair[open_] + 1, end, unit, path, units)

    def repair(self, doc):
        """Trennzeichen zwischen Tabellenfeldern ergaenzen (nach Einfuegen/Ersetzen)."""
        try:
            info = self.analyze(doc)
        except ParseError:
            return doc
        src, after = info.src, set()
        for unit in info.units:
            if unit.parent is None or unit.parent.kind != "table":
                continue
            first, _, last = unit.node
            if src.text_of(first - 1) not in (",", ";", "{"):
                after.add(first - 1)
            if src.text_of(last) not in (",", ";") and src.text_of(last + 1) not in (",", ";", "}"):
                after.add(last)
        if not after:
            return doc
        lines = list(doc.lines)
        for k in sorted(after, reverse=True):
            tok = src.sig[k]
            col = tok.end - src.starts[tok.line]
            lines[tok.line] = lines[tok.line][:col] + "," + lines[tok.line][col:]
        return doc.with_lines(lines)

    def _body(self, unit, info):
        src = info.src
        first, open_, last = unit.node
        if unit.kind != "table":
            return None
        close = src.pair[open_]
        if not src.line_end(open_) or not src.line_start(close):
            return None
        inner = src.sig[open_ + 1:close]
        indent = leading_ws(src.lines[inner[0].line]) if inner else \
            unit.indent + (self.detect_unit(None, info) or self.default_indent)
        return src.sig[open_].line + 1, src.sig[close].line, indent

    def position(self, info, pos):
        return self.simple_position(info, pos, lambda unit: self._body(unit, info))
