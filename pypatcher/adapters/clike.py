"""mode c / cpp / java - Sprachen der C-Familie (manifest.txt Abschnitte 5.6.4, 7.2).

Normalform c/1: Tokenfolge ohne Kommentare und Leerraum; Zeichenketten,
Zeichenliterale und Praeprozessorzeilen bleiben (Praeprozessorzeilen mit
vereinheitlichtem Leerraum). Einheiten: Funktionsdefinitionen, struct/union/
enum, C++-Klassen und -Methoden, namespace. Adresse mit "::" wie im Code.
Die Erkennung ist heuristisch (kein vollstaendiger C++-Parser): was nicht
eindeutig erkannt wird, ist keine Einheit und wird ueber Textbefehle geaendert.
"""

import bisect
import re
from collections import Counter

from ..errors import PatchInvalid
from ..layout import leading_ws, detect_generic
from .base import Adapter, Info, ParseError, Unit

PUNCT = sorted(("{ } ( ) [ ] ; , < > + - * / % & | ^ ! ~ ? : = . # ... -> ++ -- << >> <= >= "
                "== != && || += -= *= /= %= &= |= ^= :: .* ->* <=> <<= >>= ##").split(),
               key=len, reverse=True)
NUMBER_RE = re.compile(r"(?:\d|\.\d)(?:[eEpP][+-]|[\w.']|(?<=\d)'(?=\w))*")
RAW_RE = re.compile(r'(?:u8|u|U|L)?R"([^ ()\\\t\n]{0,16})\(')
PREFIX_RE = re.compile(r"(?:u8|u|U|L)(?=['\"])")
TYPE_WORDS = ("struct", "union", "enum", "class")
JAVA_PUNCT = sorted(PUNCT + [">>>", ">>>=", "@"], key=len, reverse=True)
JAVA_TYPES = ("class", "interface", "enum", "record")
JAVA_CONTAINERS = ("class", "interface", "enum", "record", "annotation")
CLOSE = {"(": ")", "[": "]", "{": "}"}
SPACE = " \t\r\f\v"


class Token(object):
    __slots__ = ("kind", "text", "start", "end", "line")

    def __init__(self, kind, text, start, end):
        self.kind = kind      # nl, comment, pp, str, char, num, name, punct
        self.text = text
        self.start = start
        self.end = end
        self.line = 0


def _scan_quoted(src, i, quote):
    j, n = i + 1, len(src)
    while j < n:
        ch = src[j]
        if ch == "\\":
            j += 2
            continue
        if ch == quote:
            return j + 1
        if ch == "\n":
            break
        j += 1
    raise ParseError("Zeichenkette/Zeichen ohne Ende (Position %d)" % i)


def tokenize(src, java=False):
    """java: kein Praeprozessor, "@" (Annotationen), Text-Bloecke \"\"\"...\"\"\", >>>."""
    tokens, i, n = [], 0, len(src)
    line_start = True
    punct = JAVA_PUNCT if java else PUNCT
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
        if java and src.startswith('"""', i):
            j = i + 3
            while True:
                j = src.find('"""', j)
                if j < 0:
                    raise ParseError("Text-Block ohne Ende (Position %d)" % i)
                if src[j - 1] != "\\" or src[j - 2:j] == "\\\\":
                    break
                j += 1
            i = j + 3
            tokens.append(Token("str", src[start:i], start, i))
            line_start = False
            continue
        if c == "#" and line_start and not java:
            j = i
            while j < n and src[j] != "\n":
                if src.startswith("\\\n", j):
                    j += 2
                    continue
                if src.startswith("/*", j):
                    end = src.find("*/", j + 2)
                    if end < 0:
                        raise ParseError("Kommentar ohne Ende (Position %d)" % j)
                    j = end + 2
                    continue
                if src.startswith("//", j):
                    while j < n and src[j] != "\n":
                        j += 1
                    break
                j += 1
            tokens.append(Token("pp", src[i:j], i, j))
            i = j
            continue
        line_start = False
        if src.startswith("//", i):
            j = src.find("\n", i)
            j = n if j < 0 else j
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
        raw = None if java else RAW_RE.match(src, i)
        if raw:
            end = src.find(")" + raw.group(1) + '"', raw.end())
            if end < 0:
                raise ParseError("Raw-String ohne Ende (Position %d)" % i)
            i = end + len(raw.group(1)) + 2
            tokens.append(Token("str", src[start:i], start, i))
            continue
        prefix = None if java else PREFIX_RE.match(src, i)
        if prefix:
            i = prefix.end()
            c = src[i]
        if c == '"':
            i = _scan_quoted(src, i, '"')
            kind = "str"
        elif c == "'":
            i = _scan_quoted(src, i, "'")
            kind = "char"
        elif c.isdigit() or (c == "." and i + 1 < n and src[i + 1].isdigit()):
            i = NUMBER_RE.match(src, i).end()
            kind = "num"
        elif c.isalpha() or c == "_" or c == "$" or ord(c) > 127:
            j = i + 1
            while j < n and (src[j].isalnum() or src[j] in "_$" or ord(src[j]) > 127):
                j += 1
            i = j
            kind = "name"
        elif c == "\\" and src.startswith("\\\n", i):
            i += 2
            continue
        else:
            for p in punct:
                if src.startswith(p, i):
                    break
            else:
                raise ParseError("unerwartetes Zeichen %r (Position %d)" % (c, i))
            i += len(p)
            kind = "punct"
        tokens.append(Token(kind, src[start:i], start, i))
    return tokens


def normal_tokens(tokens, keep_comments=False):
    out = []
    for tok in tokens:
        if tok.kind == "nl":
            continue
        if tok.kind == "comment":
            if keep_comments:
                out.append(tok.text)
            continue
        if tok.kind == "pp":
            text = re.sub(r"/\*.*?\*/|//.*$", " ", tok.text, flags=re.S) if not keep_comments \
                else tok.text
            out.append("\n" + " ".join(text.replace("\\\n", " ").split()) + "\n")
            continue
        out.append(tok.text)
    return " ".join(out)


class Source(object):
    def __init__(self, lines, java=False):
        self.lines = lines
        self.text = "\n".join(lines) + "\n"
        self.tokens = tokenize(self.text, java)
        starts, pos = [], 0
        for line in lines:
            starts.append(pos)
            pos += len(line) + 1
        self.starts = starts
        for tok in self.tokens:
            tok.line = bisect.bisect_right(starts, tok.start) - 1
        self.sig = [t for t in self.tokens if t.kind not in ("nl", "comment", "pp")]
        self.pair, stack = {}, []
        for k, tok in enumerate(self.sig):
            if tok.kind == "punct" and tok.text in CLOSE:
                stack.append(k)
            elif tok.kind == "punct" and tok.text in (")", "]", "}"):
                if not stack or CLOSE[self.sig[stack[-1]].text] != tok.text:
                    raise ParseError("Klammer %r passt nicht (Position %d)" % (tok.text, tok.start))
                self.pair[stack.pop()] = k
        if stack:
            raise ParseError("Klammer %r nicht geschlossen (Position %d)"
                             % (self.sig[stack[-1]].text, self.sig[stack[-1]].start))
        code = set(t.line for t in self.sig)
        self.comment_lines = set()
        self.pp_lines = set()
        for tok in self.tokens:
            if tok.kind in ("comment", "pp"):
                last = bisect.bisect_right(starts, tok.end - 1) - 1
                target = self.comment_lines if tok.kind == "comment" else self.pp_lines
                target.update(l for l in range(tok.line, last + 1) if l not in code)

    def text_of(self, k):
        return self.sig[k].text if 0 <= k < len(self.sig) else None

    def line_start(self, k):
        tok = self.sig[k]
        return not self.lines[tok.line][:tok.start - self.starts[tok.line]].strip()

    def line_end(self, k):
        if k + 1 < len(self.sig) and self.sig[k + 1].line == self.sig[k].line:
            return False
        return True


class CLikeAdapter(Adapter):
    mode = "c"
    label = "C"
    has_units = True
    default_indent = "    "
    gap_top = 1
    gap_nested = 1
    dup_re = re.compile(r"#\d+$")
    cpp = False
    java = False
    sep = "::"                  # Trenner in Einheiten-Adressen
    containers = ("namespace", "class", "struct", "union")

    # -- Normalform ------------------------------------------------------

    def normalize(self, doc, prof):
        if prof["whitespace"] == "keep":
            return doc.text()
        return normal_tokens(tokenize("\n".join(doc.lines) + "\n", self.java),
                             prof["comments"] == "keep")

    def normalize_unit(self, doc, info, unit, prof):
        if prof["whitespace"] == "keep":
            return "\n".join(info.lines[unit.start:unit.end])
        toks = [t for t in info.src.tokens if unit.start <= t.line < unit.end]
        return normal_tokens(toks, prof["comments"] == "keep")

    def validate(self, doc):
        Source(doc.lines, self.java)

    def opens_block(self, line):
        return line.rstrip().endswith("{")

    def closes_block(self, line):
        """Blockende oder Zugriffsangabe (public:/private:/protected:)."""
        return line.lstrip().startswith("}") or \
            bool(re.match(r"^\s*(public|private|protected)\b[^:]*:\s*$", line))

    def can_contain(self, unit):
        return unit.kind in self.containers

    def rename(self, rel_lines, name):
        if not re.match(r"^[A-Za-z_]\w*$", name or ""):
            raise PatchInvalid("as=%r ist kein gueltiger Name" % name)
        for i, line in enumerate(rel_lines):
            stripped = line.strip()
            if not stripped or stripped.startswith(("//", "/*", "*", "#", "template")):
                continue
            match = re.search(r"\b(struct|union|enum|class|namespace)\s+([A-Za-z_]\w*)", line) or \
                re.search(r"(~?)([A-Za-z_]\w*)\s*\(", line)
            if match:
                return rel_lines[:i] + [line[:match.start(2)] + name + line[match.end(2):]] + \
                    rel_lines[i + 1:]
            break
        raise PatchInvalid("keine Kopfzeile zum Umbenennen gefunden")

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
        src = Source(doc.lines, self.java)
        units = []
        self._scope(src, 0, len(src.sig), None, "", units)
        return Info(doc.lines, units, src=src, start_line=self._start_line(src))

    def _start_line(self, src):
        line = 0
        while line < len(src.lines) and (line in src.comment_lines or line in src.pp_lines or
                                         not src.lines[line].strip()):
            line += 1
        return line

    def _scope(self, src, lo, hi, parent, prefix, units):
        found, k = [], lo
        while k < hi:
            # Zugriffsangaben/Sprungmarken (public:, private slots:, label:) ueberspringen
            j = k
            while j < hi and src.sig[j].kind == "name":
                j += 1
            if j > k and src.text_of(j) == ":":
                k = j + 1
                continue
            first = k
            j = k
            while j < hi:                       # Anweisung bis ";" oder "{" (Klammern beachten)
                text = src.text_of(j)
                if text in ("(", "[") and j in src.pair:
                    j = src.pair[j] + 1
                    continue
                if text in (";", "{", "}"):
                    break
                j += 1
            if j >= hi:
                break
            text = src.text_of(j)
            if text in (";", "}"):
                k = j + 1
                continue
            hit = self._classify(src, first, j)
            close = src.pair[j]
            last = close
            if src.text_of(close + 1) == ";" and src.sig[close + 1].line == src.sig[close].line:
                last = close + 1
            if hit is not None and src.line_start(first) and src.line_end(last) and \
                    (first == lo or src.sig[first - 1].line < src.sig[first].line):
                name, kind = hit
                found.append((name, kind, first, j, last))
            elif hit is not None and hit[1] == "transparent":
                self._scope(src, j + 1, close, parent, prefix, units)
            k = last + 1
        names = self.label_dups([f[0] for f in found])
        for label, (name, kind, first, body, last) in zip(names, found):
            if kind == "transparent":
                self._scope(src, body + 1, src.pair[body], parent, prefix, units)
                continue
            start = src.sig[first].line
            floor = src.sig[parent.node[1]].line + 1 if parent is not None else 0
            while start - 1 >= floor and start - 1 in src.comment_lines:
                start -= 1
            path = prefix + self.sep + label if prefix else label
            if kind == "function" and parent is not None and parent.kind != "namespace":
                kind = "method"
            unit = Unit(path, kind, (first, body, last), start, src.sig[last].line + 1,
                        leading_ws(src.lines[src.sig[first].line]), parent)
            units.append(unit)
            if kind in self.containers:
                self._scope(src, body + 1, src.pair[body], unit, path, units)

    def _classify(self, src, first, brace):
        """Art und Name einer Anweisung mit Block: (name, art) oder None."""
        words = [src.text_of(k) for k in range(first, brace)]
        if not words:
            return None
        if "=" in words:                                 # Initialisierung: int a[] = {...}
            return None
        if words[0] == "extern" and len(words) == 2 and src.sig[first + 1].kind == "str":
            return ("extern", "transparent")
        lead = words[1:] if words[0] == "inline" else words
        if lead and lead[0] == "namespace":
            name = "::".join(w for w in lead[1:] if w != "::") or "(anonym)"
            return (name, "namespace") if self.cpp else None
        if words[0] == "template":
            words = self._skip_template(words)
        for word in TYPE_WORDS:
            if word in words[:3] and "(" not in words:
                pos = words.index(word)
                rest = [w for w in words[pos + 1:] if w not in ("class", "struct")]
                if rest and re.match(r"^[A-Za-z_]\w*$", rest[0]) and rest[0] not in ("final",):
                    if word == "class" and not self.cpp:
                        return None
                    return rest[0], ("struct" if word == "struct" else word)
                return None
        # Funktion: erster "(" auf oberster Ebene, davor ein Name
        for k in range(first, brace):
            if src.text_of(k) == "(" and k in src.pair:
                name = self._qualified_name(src, first, k)
                if name is None:
                    return None
                after = src.pair[k] + 1
                tail = [src.text_of(m) for m in range(after, brace)]
                if tail and tail[0] not in ("const", "noexcept", "override", "final", "->", ":",
                                            "volatile", "&", "&&", "throw", "try", "mutable",
                                            "requires", "[", "__attribute__", "=") \
                        and not self.cpp:
                    return None
                return name, "function"
        return None

    @staticmethod
    def _skip_template(words):
        depth, k = 0, 1
        while k < len(words):
            if words[k] == "<":
                depth += 1
            elif words[k] == ">":
                depth -= 1
                if depth == 0:
                    return words[k + 1:]
            elif words[k] == ">>":
                depth -= 2
                if depth <= 0:
                    return words[k + 1:]
            k += 1
        return words

    def _qualified_name(self, src, first, paren):
        k = paren - 1
        if k < first:
            return None
        parts = []
        tok = src.sig[k]
        if tok.kind == "name":
            parts.append(tok.text)
            k -= 1
            if k >= first and src.text_of(k) == "~":
                parts[-1] = "~" + parts[-1]
                k -= 1
        elif k - 1 >= first and src.text_of(k - 1) == "operator":
            parts.append("operator" + tok.text)
            k -= 2
        elif src.text_of(k) == "operator":
            return None
        else:
            return None
        if parts[0] in ("if", "for", "while", "switch", "return", "sizeof", "catch"):
            return None
        while k - 1 >= first and src.text_of(k) == "::":
            j = k - 1
            if src.text_of(j) in (">", ">>"):            # Box<int>::f -> Box::f
                depth = 0
                while j >= first:
                    text = src.text_of(j)
                    depth += {">": 1, ">>": 2, "<": -1}.get(text, 0)
                    j -= 1
                    if depth <= 0 and text == "<":
                        break
            if j < first or src.sig[j].kind != "name":
                break
            parts.insert(0, src.text_of(j))
            k = j - 1
        return "::".join(parts)

    def _body(self, unit, info):
        src = info.src
        first, body, last = unit.node
        close = src.pair[body]
        if not src.line_end(body) or not src.line_start(close):
            return None
        inner = src.sig[body + 1:close]
        indent = leading_ws(src.lines[inner[0].line]) if inner else \
            unit.indent + (self.detect_unit(None, info) or self.default_indent)
        return src.sig[body].line + 1, src.sig[close].line, indent

    def position(self, info, pos):
        return self.simple_position(info, pos, lambda unit: self._body(unit, info))


class CAdapter(CLikeAdapter):
    mode = "c"
    label = "C"
    cpp = False


class CppAdapter(CLikeAdapter):
    mode = "cpp"
    label = "C++"
    cpp = True


class JavaAdapter(CLikeAdapter):
    """Java (R032): Klassen, Interfaces, Enums, Records, Annotationstypen,
    Methoden und Konstruktoren; Adressen mit "." (Klasse.Innen.methode),
    Ueberladungen name#1, name#2. Annotationen und Javadoc gehoeren zur
    Einheit."""
    mode = "java"
    label = "Java"
    java = True
    sep = "."
    containers = JAVA_CONTAINERS

    def closes_block(self, line):
        return line.lstrip().startswith("}")

    def _classify(self, src, first, brace):
        k = first
        while src.text_of(k) == "@" and src.text_of(k + 1) != "interface":
            k += 2                                   # @Name, @a.b.Name, @Name(...)
            while src.text_of(k) == "." and k + 1 < brace:
                k += 2
            if src.text_of(k) == "(" and k in src.pair:
                k = src.pair[k] + 1
        words = [src.text_of(m) for m in range(k, brace)]
        if not words or "=" in words:
            return None
        if "@" in words and words[words.index("@") + 1:][:1] == ["interface"]:
            pos = words.index("@") + 2
            return (words[pos], "annotation") if pos < len(words) else None
        for word in JAVA_TYPES:
            if word in words:
                pos = words.index(word)
                if pos + 1 < len(words) and re.match(r"^[A-Za-z_$][\w$]*$", words[pos + 1]) and \
                        all(w not in ("(", "new") for w in words[:pos]):
                    return words[pos + 1], word
        for m in range(k, brace):
            if src.text_of(m) == "(" and m in src.pair:
                tok = src.sig[m - 1] if m - 1 >= k else None
                if tok is None or tok.kind != "name" or tok.text in (
                        "if", "for", "while", "switch", "catch", "synchronized", "try", "return",
                        "new", "super", "this"):
                    return None
                tail = [src.text_of(t) for t in range(src.pair[m] + 1, brace)]
                if tail and tail[0] not in ("throws",):
                    return None
                return tok.text, "function"
        return None

    def rename(self, rel_lines, name):
        if not re.match(r"^[A-Za-z_$][\w$]*$", name or ""):
            raise PatchInvalid("as=%r ist kein gueltiger Name" % name)
        for i, line in enumerate(rel_lines):
            stripped = line.strip()
            if not stripped or stripped.startswith(("//", "/*", "*", "@")) and \
                    not stripped.startswith("@interface"):
                continue
            match = re.search(r"\b(class|interface|enum|record)\s+([A-Za-z_$][\w$]*)", line) or \
                re.search(r"()([A-Za-z_$][\w$]*)\s*\(", line)
            if match:
                return rel_lines[:i] + [line[:match.start(2)] + name + line[match.end(2):]] + \
                    rel_lines[i + 1:]
            break
        raise PatchInvalid("keine Kopfzeile zum Umbenennen gefunden")
