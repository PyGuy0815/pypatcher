"""mode js - JavaScript (manifest.txt Abschnitte 5.6, 7.3).

Normalform js/1: Tokenfolge ohne Kommentare und Einrueckung. Zeichenketten,
Template-Strings und regulaere Ausdruecke bleiben exakt. Ein Zeilenumbruch
zaehlt nur dort, wo er wegen der automatischen Semikolon-Einfuegung (ASI)
die Bedeutung aendern kann.
Einheiten: Funktionen, Klassen, Methoden, Funktionen in const/let/var.
"""

import bisect
import re
from collections import Counter

from ..errors import PatchInvalid
from ..layout import leading_ws, detect_generic
from .base import Adapter, Info, ParseError, Unit

PUNCT = sorted(("{ } ( ) [ ] ; , < > + - * / % & | ^ ! ~ ? : = . @ # => == != <= >= && || "
                "?? ?. ++ -- += -= *= /= %= &= |= ^= ** << >> === !== **= <<= >>= >>> "
                "&&= ||= ??= ... >>>=").split(), key=len, reverse=True)
NUMBER_RE = re.compile(r"(?:0[xX][0-9a-fA-F_]+|0[oO][0-7_]+|0[bB][01_]+|"
                       r"(?:\d[\d_]*\.?[\d_]*|\.\d[\d_]*)(?:[eE][+-]?\d[\d_]*)?)n?")
# nach diesen Woertern beginnt ein Ausdruck -> "/" leitet einen regulaeren Ausdruck ein
EXPR_KEYWORDS = {"return", "typeof", "instanceof", "in", "of", "new", "delete", "void",
                 "throw", "case", "do", "else", "yield", "await"}
# Zeilenumbruch nach diesen Woertern beendet die Anweisung (ASI, "restricted productions")
RESTRICTED = {"return", "throw", "break", "continue", "yield", "async"}
# Zeilenumbruch vor diesen Zeichen kann die Bedeutung aendern (Fortsetzung vs. neue Anweisung)
NL_STARTERS = {"(", "[", "+", "-", "/", "++", "--", "`", "*", ".", "?.", ",", "=", "=>"}
SPACE = " \t\r\f\v ﻿"
NEWLINES = "\n  "
CLOSE = {"(": ")", "[": "]", "{": "}"}

RENAME_RES = [re.compile(p) for p in (
    r"^(\t*(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s*\*?\s*)([A-Za-z_$][\w$]*)",
    r"^(\t*(?:export\s+)?(?:default\s+)?class\s+)([A-Za-z_$][\w$]*)",
    r"^(\t*(?:export\s+)?(?:const|let|var)\s+)([A-Za-z_$][\w$]*)",
    r"^(\t*(?:static\s+)?(?:async\s+)?(?:get\s+|set\s+)?\*?\s*#?)([A-Za-z_$][\w$]*)(?=\s*[(=])",
)]


class Token(object):
    __slots__ = ("kind", "text", "start", "end", "line", "nl")

    def __init__(self, kind, text, start, end):
        self.kind = kind      # nl, comment, str, tmpl, regex, num, name, punct
        self.text = text
        self.start = start
        self.end = end
        self.line = 0
        self.nl = "\n" in text if kind == "comment" else False


def _scan_string(src, i):
    quote, j, n = src[i], i + 1, len(src)
    while j < n:
        ch = src[j]
        if ch == "\\":
            j += 2
            continue
        if ch == quote:
            return j + 1
        if ch in NEWLINES:
            break
        j += 1
    raise ParseError("Zeichenkette ohne Ende (Position %d)" % i)


def _scan_template(src, i):
    """i zeigt hinter das oeffnende `; liefert die Position hinter dem schliessenden `."""
    j, n = i, len(src)
    while j < n:
        ch = src[j]
        if ch == "\\":
            j += 2
        elif ch == "`":
            return j + 1
        elif src.startswith("${", j):
            _, j = _tokenize(src, j + 2, in_template=True)
            j += 1
        else:
            j += 1
    raise ParseError("Template-String ohne Ende (Position %d)" % (i - 1))


def _scan_regex(src, i):
    j, n, in_class = i + 1, len(src), False
    while j < n:
        ch = src[j]
        if ch == "\\":
            j += 2
            continue
        if ch in NEWLINES:
            break
        if ch == "[":
            in_class = True
        elif ch == "]":
            in_class = False
        elif ch == "/" and not in_class:
            j += 1
            while j < n and (src[j].isalnum() or src[j] in "_$"):
                j += 1
            return j
        j += 1
    raise ParseError("Regulaerer Ausdruck ohne Ende (Position %d)" % i)


def _regex_allowed(last):
    if last is None:
        return True
    if last.kind == "punct":
        return last.text not in (")", "]", "}")
    return last.kind == "name" and last.text in EXPR_KEYWORDS


def _tokenize(src, i=0, in_template=False):
    tokens, n, depth, last = [], len(src), 0, None
    if i == 0 and src.startswith("#!"):
        j = src.find("\n")
        j = n if j < 0 else j
        tokens.append(Token("comment", src[:j], 0, j))
        i = j
    while i < n:
        c = src[i]
        if c in NEWLINES:
            tokens.append(Token("nl", c, i, i + 1))
            i += 1
            continue
        if c in SPACE:
            i += 1
            continue
        start = i
        if src.startswith("//", i):
            j = i
            while j < n and src[j] not in NEWLINES:
                j += 1
            tokens.append(Token("comment", src[i:j], i, j))
            i = j
            continue
        if src.startswith("/*", i):
            j = src.find("*/", i + 2)
            if j < 0:
                raise ParseError("Kommentar ohne Ende (Position %d)" % i)
            tokens.append(Token("comment", src[i:j + 2], i, j + 2))
            i = j + 2
            continue
        if c in "'\"":
            i = _scan_string(src, i)
            kind = "str"
        elif c == "`":
            i = _scan_template(src, i + 1)
            kind = "tmpl"
        elif c == "/" and _regex_allowed(last):
            i = _scan_regex(src, i)
            kind = "regex"
        elif c.isdigit() or (c == "." and i + 1 < n and src[i + 1].isdigit()):
            i = NUMBER_RE.match(src, i).end()
            kind = "num"
        elif c.isalpha() or c in "_$\\" or ord(c) > 127:
            j = i + 1
            while j < n and (src[j].isalnum() or src[j] in "_$\\" or
                             (ord(src[j]) > 127 and src[j] not in SPACE + NEWLINES)):
                j += 1
            i = j
            kind = "name"
        else:
            for p in PUNCT:
                if src.startswith(p, i):
                    if p == "?." and i + 2 < n and src[i + 2].isdigit():
                        continue
                    break
            else:
                raise ParseError("unerwartetes Zeichen %r (Position %d)" % (c, i))
            if in_template:
                if p == "{":
                    depth += 1
                elif p == "}":
                    if depth == 0:
                        return tokens, i
                    depth -= 1
            i += len(p)
            kind = "punct"
        tok = Token(kind, src[start:i], start, i)
        tokens.append(tok)
        last = tok
    if in_template:
        raise ParseError("Template-Ausdruck ohne Ende")
    return tokens, i


def tokenize(src):
    return _tokenize(src)[0]


def normal_tokens(tokens, keep_comments=False):
    """Normalform aus Tokens: ein Leerzeichen zwischen Tokens, Zeilenumbruch nur wo ASI-relevant."""
    out, pending, prev = [], False, None
    for tok in tokens:
        if tok.kind == "nl":
            pending = True
            continue
        if tok.kind == "comment":
            if tok.nl:
                pending = True
            if keep_comments:
                out.append(tok.text)
            continue
        if pending and prev is not None and (prev.text in RESTRICTED or tok.text in NL_STARTERS
                                             or tok.kind in ("tmpl", "regex")):
            out.append("\\n")
        pending = False
        out.append(tok.text)
        prev = tok
    return " ".join(out)


def check_balance(tokens):
    stack = []
    for tok in tokens:
        if tok.kind != "punct":
            continue
        if tok.text in CLOSE:
            stack.append(tok)
        elif tok.text in (")", "]", "}"):
            if not stack or CLOSE[stack[-1].text] != tok.text:
                raise ParseError("Klammer %r passt nicht (Position %d)" % (tok.text, tok.start))
            stack.pop()
    if stack:
        raise ParseError("Klammer %r nicht geschlossen (Position %d)" % (stack[-1].text, stack[-1].start))


class Source(object):
    """Quelltext mit Tokens, Zeilen und Klammerpaaren."""

    def __init__(self, lines):
        self.lines = lines
        self.text = "\n".join(lines) + "\n"
        self.tokens = tokenize(self.text)
        check_balance(self.tokens)
        starts, pos = [], 0
        for line in lines:
            starts.append(pos)
            pos += len(line) + 1
        self.starts = starts
        for tok in self.tokens:
            tok.line = bisect.bisect_right(starts, tok.start) - 1
        self.sig = [t for t in self.tokens if t.kind not in ("nl", "comment")]
        self.pair, stack = {}, []
        for k, tok in enumerate(self.sig):
            if tok.kind == "punct" and tok.text in CLOSE:
                stack.append(k)
            elif tok.kind == "punct" and tok.text in (")", "]", "}"):
                self.pair[stack.pop()] = k
        self.comment_lines = self._comment_only_lines()

    def _comment_only_lines(self):
        code = set(t.line for t in self.sig)
        out = set()
        for tok in self.tokens:
            if tok.kind == "comment":
                last = bisect.bisect_right(self.starts, tok.end - 1) - 1
                for line in range(tok.line, last + 1):
                    if line not in code:
                        out.add(line)
        return out

    def line_start(self, k):
        """Ist sig[k] das erste Code-Token seiner Zeile (davor nur Leerraum)?"""
        tok = self.sig[k]
        return not self.lines[tok.line][:tok.start - self.starts[tok.line]].strip()

    def line_end(self, k):
        """Folgt nach sig[k] in derselben Zeile kein weiteres Code-Token?"""
        return k + 1 >= len(self.sig) or self.sig[k + 1].line > self.sig[k].line

    def text_of(self, k):
        return self.sig[k].text if 0 <= k < len(self.sig) else None


class JsAdapter(Adapter):
    mode = "js"
    label = "JavaScript"
    has_units = True
    default_indent = "  "
    gap_top = 1
    gap_nested = 1

    # -- Normalform ------------------------------------------------------

    @staticmethod
    def _exact(prof):
        return prof["whitespace"] == "keep"

    def normalize(self, doc, prof):
        if self._exact(prof):
            return doc.text()
        return normal_tokens(tokenize("\n".join(doc.lines) + "\n"), prof["comments"] == "keep")

    def normalize_unit(self, doc, info, unit, prof):
        if self._exact(prof):
            return "\n".join(info.lines[unit.start:unit.end])
        toks = [t for t in info.src.tokens if unit.start <= t.line < unit.end]
        return normal_tokens(toks, prof["comments"] == "keep")

    def validate(self, doc):
        Source(doc.lines)

    def opens_block(self, line):
        return line.rstrip().endswith(("{", "(", "["))

    def closes_block(self, line):
        return line.lstrip().startswith(("}", ")", "]"))

    def can_contain(self, unit):
        return unit.kind == "class"

    def rename(self, rel_lines, name):
        if not re.match(r"^[A-Za-z_$][\w$]*$", name or ""):
            raise PatchInvalid("as=%r ist kein gueltiger Name" % name)
        for i, line in enumerate(rel_lines):
            stripped = line.lstrip()
            if stripped.startswith(("//", "/*", "*", "@")):
                continue
            for regex in RENAME_RES:
                match = regex.match(line)
                if match:
                    return rel_lines[:i] + [match.group(1) + name + line[match.end(2):]] + \
                        rel_lines[i + 1:]
            break
        raise PatchInvalid("keine Funktions-/Klassenzeile zum Umbenennen gefunden")

    def detect_unit(self, doc, info=None):
        try:
            info = info or self.analyze(doc)
        except ParseError:
            return detect_generic(doc.lines)
        src, diffs = info.src, Counter()
        for k, tok in enumerate(src.sig):
            if tok.text == "{" and src.line_end(k) and k in src.pair:
                inner = next((t for t in src.sig[k + 1:src.pair[k]] if t.line > tok.line), None)
                if inner is None:
                    continue
                outer = leading_ws(src.lines[tok.line])
                ws = leading_ws(src.lines[inner.line])
                if ws.startswith(outer) and len(ws) > len(outer):
                    diffs[ws[len(outer):]] += 1
        return diffs.most_common(1)[0][0] if diffs else detect_generic(doc.lines)

    # -- Einheiten -------------------------------------------------------

    def analyze(self, doc):
        src = Source(doc.lines)
        units = []
        self._scope(src, 0, len(src.sig), None, "", units)
        return Info(doc.lines, units, src=src, start_line=self._start_line(src))

    def _start_line(self, src):
        """Hinter Kopfkommentaren und import-Anweisungen."""
        line = 0
        while line < len(src.lines) and (line in src.comment_lines or not src.lines[line].strip()):
            line += 1
        k, n = 0, len(src.sig)
        while k < n and src.text_of(k) == "import" and src.text_of(k + 1) not in ("(", "."):
            seen_str, j = False, k + 1
            while j < n:
                tok = src.sig[j]
                if tok.text == ";":
                    j += 1
                    break
                if seen_str and tok.line > src.sig[j - 1].line:
                    break
                if tok.kind == "str":
                    seen_str = True
                j += 1
            line = src.sig[j - 1].line + 1
            k = j
        return line

    def _scope(self, src, lo, hi, parent, prefix, units):
        """Einheiten in sig[lo:hi] (oberste Ebene bzw. Klassenrumpf)."""
        found, k = [], lo
        while k < hi:
            hit = self._class_member(src, k, hi) if parent is not None else self._statement(src, k, hi)
            if hit is None:
                k = self._skip(src, k, hi)
                continue
            name, kind, first, body, last = hit
            found.append((name, kind, first, body, last))
            k = last + 1
        for label, (name, kind, first, body, last) in zip(
                self.label_dups([f[0] for f in found]), found):
            start = self._with_comments(src, src.sig[first].line, parent)
            end = src.sig[last].line + 1
            indent = leading_ws(src.lines[src.sig[first].line])
            path = prefix + "." + label if prefix else label
            unit = Unit(path, kind, (first, body, last), start, end, indent, parent)
            units.append(unit)
            if kind == "class":
                self._scope(src, body + 1, src.pair[body], unit, path, units)

    def _skip(self, src, k, hi):
        """Naechstes Token auf gleicher Ebene (Klammern werden uebersprungen)."""
        if k in src.pair:
            return src.pair[k] + 1
        return k + 1

    def _with_comments(self, src, line, parent):
        floor = src.sig[parent.node[1]].line + 1 if parent is not None else 0
        while line - 1 >= floor and (line - 1 in src.comment_lines or
                                     src.lines[line - 1].lstrip().startswith("@")):
            line -= 1
        return line

    def _block_end(self, src, body, hi):
        """Ende einer Einheit: schliessende Klammer, ggf. mit ";" in derselben Zeile."""
        last = src.pair.get(body)
        if last is None or last >= hi:
            return None
        if src.text_of(last + 1) == ";" and src.sig[last + 1].line == src.sig[last].line:
            last += 1
        return last if src.line_end(last) else None

    def _statement(self, src, k, hi):
        if not src.line_start(k) or (k > 0 and src.sig[k - 1].line == src.sig[k].line):
            return None
        first, j = k, k
        if src.text_of(j) == "export":
            j += 1
            if src.text_of(j) == "default":
                j += 1
        if src.text_of(j) == "@":
            return None
        word = src.text_of(j)
        if word == "async" and src.text_of(j + 1) == "function":
            j += 1
            word = "function"
        if word == "function":
            j += 1
            if src.text_of(j) == "*":
                j += 1
            if j >= hi or src.sig[j].kind != "name":
                return None
            name = src.sig[j].text
            return self._function_rest(src, first, j + 1, hi, name, "function")
        if word == "class":
            return self._class_rest(src, first, j + 1, hi)
        if word in ("const", "let", "var"):
            j += 1
            if j >= hi or src.sig[j].kind != "name" or src.text_of(j + 1) != "=":
                return None
            name = src.sig[j].text
            j += 2
            if src.text_of(j) == "class":
                hit = self._class_rest(src, first, j + 1, hi, name)
                return hit
            return self._callable(src, first, j, hi, name, "function")
        return None

    def _function_rest(self, src, first, j, hi, name, kind):
        """j zeigt auf "(" der Parameterliste."""
        if src.text_of(j) != "(" or j not in src.pair:
            return None
        body = src.pair[j] + 1
        if src.text_of(body) != "{":
            return None
        last = self._block_end(src, body, hi)
        return None if last is None else (name, kind, first, body, last)

    def _class_rest(self, src, first, j, hi, name=None):
        if name is None:
            if j >= hi or src.sig[j].kind != "name" or src.text_of(j) == "extends":
                return None
            name = src.sig[j].text
            j += 1
        while j < hi and src.text_of(j) != "{":
            j = self._skip(src, j, hi)
        if j >= hi:
            return None
        last = self._block_end(src, j, hi)
        return None if last is None else (name, "class", first, j, last)

    def _callable(self, src, first, j, hi, name, kind):
        """Wert einer Zuweisung: function ..., (..) => {..}, x => {..}, async ..."""
        if src.text_of(j) == "async":
            j += 1
        if src.text_of(j) == "function":
            j += 1
            if src.text_of(j) == "*":
                j += 1
            if j < hi and src.sig[j].kind == "name":
                j += 1
            return self._function_rest(src, first, j, hi, name, kind)
        if src.text_of(j) == "(" and j in src.pair:
            j = src.pair[j] + 1
        elif j < hi and src.sig[j].kind == "name":
            j += 1
        else:
            return None
        if src.text_of(j) != "=>" or src.text_of(j + 1) != "{":
            return None
        last = self._block_end(src, j + 1, hi)
        return None if last is None else (name, kind, first, j + 1, last)

    def _class_member(self, src, k, hi):
        if not src.line_start(k) or src.sig[k - 1].line == src.sig[k].line:
            return None
        first, j = k, k
        while src.text_of(j) in ("static", "async", "get", "set", "*") and \
                src.text_of(j + 1) not in ("(", "="):
            j += 1
        private = src.text_of(j) == "#"
        if private:
            j += 1
        if j >= hi or src.sig[j].kind != "name":
            return None
        name = ("#" if private else "") + src.sig[j].text
        if src.text_of(j + 1) == "(":
            return self._function_rest(src, first, j + 1, hi, name, "method")
        if src.text_of(j + 1) == "=":
            return self._callable(src, first, j + 2, hi, name, "method")
        return None

    def _body(self, unit, info):
        src = info.src
        first, body, last = unit.node
        close = src.pair[body]
        if not src.line_end(body) or not src.line_start(close):
            return None
        inner = [t for t in src.sig[body + 1:close]]
        if inner:
            indent = leading_ws(src.lines[inner[0].line])
        else:
            indent = unit.indent + (self.detect_unit(None, info) or self.default_indent)
        return src.sig[body].line + 1, src.sig[close].line, indent

    def position(self, info, pos):
        return self.simple_position(info, pos, lambda unit: self._body(unit, info))
