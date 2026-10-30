"""mode json - JSON-Daten (manifest.txt Abschnitte 5.6.4, 7.2).

Normalform json/1: die Daten selbst - Formatierung zaehlt nicht, die
Reihenfolge der Schluessel standardmaessig auch nicht (keyorder=keep:
zaehlt). Zahlen bleiben in ihrer Art (1 und 1.0 sind verschieden).
Einheiten: Eintraege von Objekten und Listen ueber ihren Pfad, z.B.
"server/port" oder "plugins/0/name" (JSON-Pointer ohne fuehrendes "/").
Nach Einfuegen/Loeschen/Ersetzen werden fehlende bzw. ueberzaehlige Kommas
an den Grenzen ergaenzt bzw. entfernt (repair) - sonst waere die Datei kein
gueltiges JSON.
"""

import bisect
import json
import re
from collections import Counter

from ..errors import PatchInvalid
from ..layout import leading_ws, detect_generic
from .base import Adapter, Info, ParseError, Unit

WS = " \t\r\n"
NUMBER_RE = re.compile(r"-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?")
VALUE_START = '"{[-0123456789tfn'


class Node(object):
    """Wert mit Position; bei Objekten/Listen mit Eintraegen (Member)."""

    def __init__(self, kind, start, end=None):
        self.kind = kind          # object, array, value
        self.start = start
        self.end = end
        self.members = []


class Member(object):
    def __init__(self, key, start, value, end, comma):
        self.key = key            # str (Objekt) oder int (Liste)
        self.start = start        # Schluessel bzw. Listenelement
        self.value = value
        self.end = end            # Ende des Werts
        self.comma = comma        # Position des folgenden Kommas oder None


class _Parser(object):
    def __init__(self, text):
        self.text = text
        self.pos = 0

    def fail(self, msg):
        line = self.text.count("\n", 0, self.pos) + 1
        raise ParseError("Zeile %d: %s" % (line, msg))

    def ws(self):
        while self.pos < len(self.text) and self.text[self.pos] in WS:
            self.pos += 1

    def string(self):
        start, i = self.pos, self.pos + 1
        while i < len(self.text):
            ch = self.text[i]
            if ch == "\\":
                i += 2
                continue
            if ch == '"':
                self.pos = i + 1
                try:
                    return json.loads(self.text[start:i + 1])
                except ValueError as exc:
                    self.fail("Zeichenkette ungueltig (%s)" % exc)
            if ch == "\n":
                break
            i += 1
        self.fail("Zeichenkette ohne Ende")

    def value(self):
        self.ws()
        if self.pos >= len(self.text):
            self.fail("Wert erwartet")
        ch, start = self.text[self.pos], self.pos
        if ch == "{":
            return self.container("object", "}")
        if ch == "[":
            return self.container("array", "]")
        if ch == '"':
            self.string()
        else:
            for word in ("true", "false", "null"):
                if self.text.startswith(word, self.pos):
                    self.pos += len(word)
                    break
            else:
                match = NUMBER_RE.match(self.text, self.pos)
                if not match or match.end() == self.pos:
                    self.fail("Wert erwartet")
                self.pos = match.end()
        return Node("value", start, self.pos)

    def container(self, kind, close):
        node = Node(kind, self.pos)
        self.pos += 1
        self.ws()
        if self.text.startswith(close, self.pos):
            self.pos += 1
            node.end = self.pos
            return node
        index = 0
        while True:
            self.ws()
            start = self.pos
            if kind == "object":
                if not self.text.startswith('"', self.pos):
                    self.fail("Schluessel erwartet")
                key = self.string()
                self.ws()
                if not self.text.startswith(":", self.pos):
                    self.fail("':' erwartet")
                self.pos += 1
            else:
                key = index
            value = self.value()
            member = Member(key, start, value, value.end, None)
            node.members.append(member)
            index += 1
            self.ws()
            if self.text.startswith(",", self.pos):
                member.comma = self.pos
                self.pos += 1
                continue
            if self.text.startswith(close, self.pos):
                self.pos += 1
                node.end = self.pos
                return node
            self.fail("',' oder '%s' erwartet" % close)


def parse(text):
    parser = _Parser(text)
    root = parser.value()
    parser.ws()
    if parser.pos != len(text):
        parser.fail("Text nach dem Ende der Daten")
    return root


def _canon(value, keep_order):
    if isinstance(value, _Pairs):
        items = value if keep_order else sorted(value, key=lambda kv: kv[0])
        return "{" + ",".join(json.dumps(k, ensure_ascii=False) + ":" + _canon(v, keep_order)
                              for k, v in items) + "}"
    if isinstance(value, list):
        return "[" + ",".join(_canon(v, keep_order) for v in value) + "]"
    return json.dumps(value, ensure_ascii=False)


class _Pairs(list):
    """Objekt als Liste von (Schluessel, Wert) - behaelt Reihenfolge und Dubletten."""


def canonical(text, keep_order):
    try:
        data = json.loads(text, object_pairs_hook=_Pairs)
    except ValueError as exc:
        raise ParseError(str(exc))
    return _canon(data, keep_order)


def _escape(key):
    return str(key).replace("~", "~0").replace("/", "~1")


class JsonAdapter(Adapter):
    mode = "json"
    label = "JSON"
    has_units = True
    default_indent = "  "
    gap_top = 0
    gap_nested = 0

    def _text(self, lines):
        return "\n".join(lines) + "\n"

    def normalize(self, doc, prof):
        if prof["whitespace"] == "keep":
            return doc.text()
        return canonical(self._text(doc.lines), prof["keyorder"] == "keep")

    def normalize_unit(self, doc, info, unit, prof):
        if prof["whitespace"] == "keep":
            return "\n".join(info.lines[unit.start:unit.end])
        member = unit.node
        value = canonical(info.text[member.value.start:member.value.end], prof["keyorder"] == "keep")
        return json.dumps(member.key, ensure_ascii=False) + ":" + value

    def validate(self, doc):
        text = self._text(doc.lines)
        parse(text)
        canonical(text, True)

    def repair(self, doc):
        """Kommas an Zeilengrenzen ergaenzen/entfernen, bis die Datei gueltig ist."""
        try:
            parse(self._text(doc.lines))
            return doc
        except ParseError:
            pass
        lines = list(doc.lines)
        rows = [i for i, l in enumerate(lines) if l.strip()]
        for a, b in zip(rows, rows[1:]):
            cur = lines[a].rstrip()
            tail = lines[a][len(cur):]
            nxt = lines[b].lstrip()
            if cur.endswith(",") and nxt[:1] in "}]":
                lines[a] = cur[:-1] + tail
            elif cur and cur[-1] not in ",{[:" and nxt[:1] in VALUE_START:
                lines[a] = cur + "," + tail
        return doc.with_lines(lines)

    def can_contain(self, unit):
        return unit.node.value.kind in ("object", "array")

    def rename(self, rel_lines, name):
        for i, line in enumerate(rel_lines):
            match = re.match(r'^(\s*)"((?:[^"\\]|\\.)*)"(\s*:)', line)
            if match:
                key = json.dumps(name, ensure_ascii=False)
                return rel_lines[:i] + [match.group(1) + key + line[match.start(3):]] + \
                    rel_lines[i + 1:]
        raise PatchInvalid("kein Schluessel zum Umbenennen gefunden")

    def detect_unit(self, doc, info=None):
        try:
            info = info or self.analyze(doc)
        except ParseError:
            return detect_generic(doc.lines)
        diffs = Counter()
        for unit in info.units:
            parent = unit.parent
            if parent is not None and unit.start > parent.start:
                outer = leading_ws(info.lines[parent.start])
                if unit.indent.startswith(outer) and len(unit.indent) > len(outer):
                    diffs[unit.indent[len(outer):]] += 1
        if not diffs and info.units:
            ws = info.units[0].indent
            if ws:
                return ws
        return diffs.most_common(1)[0][0] if diffs else detect_generic(doc.lines)

    # -- Einheiten -------------------------------------------------------

    def analyze(self, doc):
        text = self._text(doc.lines)
        root = parse(text)
        starts, pos = [], 0
        for line in doc.lines:
            starts.append(pos)
            pos += len(line) + 1
        info = Info(doc.lines, [], text=text, root=root, starts=starts)
        self._collect(info, root, None, "")
        return info

    def _line(self, info, offset):
        return bisect.bisect_right(info.starts, offset) - 1

    def _alone_before(self, info, offset):
        line = self._line(info, offset)
        return not info.text[info.starts[line]:offset].strip()

    def _alone_after(self, info, offset):
        nl = info.text.find("\n", offset)
        return not info.text[offset:nl].strip()

    def _collect(self, info, node, parent, prefix):
        labels = self.label_dups([_escape(m.key) for m in node.members])
        for label, member in zip(labels, node.members):
            end = member.comma + 1 if member.comma is not None and \
                self._line(info, member.comma) == self._line(info, member.end - 1) else member.end
            path = prefix + "/" + label if prefix else label
            if not (self._alone_before(info, member.start) and self._alone_after(info, end)):
                continue
            start = self._line(info, member.start)
            unit = Unit(path, "member" if node.kind == "object" else "item", member, start,
                        self._line(info, end - 1) + 1, leading_ws(info.lines[start]), parent)
            unit.container = node
            info.units.append(unit)
            if member.value.kind in ("object", "array"):
                self._collect(info, member.value, unit, path)

    def _body(self, node, info, indent):
        if node.kind not in ("object", "array"):
            return None
        if not self._alone_after(info, node.start + 1) or not self._alone_before(info, node.end - 1):
            return None
        first = self._line(info, node.start) + 1
        last = self._line(info, node.end - 1)
        if node.members:
            indent = leading_ws(info.lines[self._line(info, node.members[0].start)])
        return first, last, indent

    def position(self, info, pos):
        if pos in ("start", "end"):
            root = info.root
            unit = self.detect_unit(None, info) or self.default_indent
            body = self._body(root, info, unit)
            if body is None:
                raise PatchInvalid("Position %r: Wurzel ist nicht zeilenweise abgrenzbar" % pos)
            return (body[0] if pos == "start" else body[1]), body[2], True
        return self.simple_position(info, pos, lambda u: self._body(
            u.node.value, info, u.indent + (self.detect_unit(None, info) or self.default_indent)))
