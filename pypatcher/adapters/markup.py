"""mode html / xml - Auszeichnungssprachen (manifest.txt Abschnitte 5.6.3, 7.2).

Normalform html/1, xml/1: Folge aus Tags, Text, Deklarationen. Attribute
sortiert (Reihenfolge und Anfuehrungszeichen egal), Entitaeten aufgeloest,
Kommentare zaehlen nicht. Leerraum im Text zaehlt standardmaessig
(whitespace=keep). Inhalt von <script>/<style> ueber die JS-/CSS-Normalform.
Einheiten: Elemente, die zeilenweise abgrenzbar sind; Adresse robust ueber
id bzw. Tag + Klassen + Nummer.
"""

import bisect
import re
import xml.parsers.expat
from collections import Counter
from html.parser import HTMLParser

from ..layout import leading_ws, detect_generic
from .base import Adapter, Info, ParseError, Unit

VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta",
        "param", "source", "track", "wbr"}
KEEP_SPACE = {"pre", "textarea", "script", "style"}
JS_TYPES = {"", "text/javascript", "application/javascript", "module", "text/ecmascript",
            "application/ecmascript"}


class Element(object):
    def __init__(self, tag, attrs, start, start_end, parent):
        self.tag = tag
        self.attrs = attrs
        self.start = start
        self.start_end = start_end
        self.end_start = None      # Beginn des End-Tags
        self.end = None            # Ende des Elements (None: nicht ausdruecklich beendet)
        self.parent = parent
        self.children = []
        self.path = None


class Tree(object):
    """Elemente und Ereignisse mit Zeichen-Positionen."""

    def __init__(self, lines):
        self.lines = lines
        self.text = "\n".join(lines) + "\n"
        self.starts, pos = [], 0
        for line in lines:
            self.starts.append(pos)
            pos += len(line) + 1
        self.events = []           # (offset, art, daten)
        self.elements = []
        self.roots = []
        self.comment_spans = []

    def line(self, offset):
        return bisect.bisect_right(self.starts, offset) - 1

    def alone_before(self, offset):
        line = self.line(offset)
        return not self.text[self.starts[line]:offset].strip()

    def alone_after(self, offset):
        """Nach Position offset (exklusiv) bis Zeilenende nur Leerraum?"""
        nl = self.text.find("\n", offset)
        return not self.text[offset:nl if nl >= 0 else len(self.text)].strip()

    def add_element(self, tag, attrs, start, start_end, stack):
        parent = stack[-1] if stack else None
        el = Element(tag, attrs, start, start_end, parent)
        (parent.children if parent else self.roots).append(el)
        self.elements.append(el)
        return el

    def comment_lines(self):
        code = set()
        out = set()
        for start, end in self.comment_spans:
            out.update(range(self.line(start), self.line(end - 1) + 1))
        for offset, kind, _ in self.events:
            if kind != "comment" and not (kind == "data" and not _.strip()):
                code.add(self.line(offset))
        return out - code


class _HtmlBuilder(HTMLParser):
    def __init__(self, tree):
        HTMLParser.__init__(self, convert_charrefs=True)
        self.tree = tree
        self.stack = []

    def _pos(self):
        line, col = self.getpos()
        return self.tree.starts[line - 1] + col

    def handle_starttag(self, tag, attrs):
        start = self._pos()
        end = start + len(self.get_starttag_text())
        el = self.tree.add_element(tag, attrs, start, end, self.stack)
        self.tree.events.append((start, "start", (tag, attrs)))
        if tag in VOID:
            el.end = end
            self.tree.events.append((end, "end", tag))
        else:
            self.stack.append(el)

    def handle_startendtag(self, tag, attrs):
        start = self._pos()
        end = start + len(self.get_starttag_text())
        el = self.tree.add_element(tag, attrs, start, end, self.stack)
        el.end = end
        self.tree.events.append((start, "start", (tag, attrs)))
        self.tree.events.append((end, "end", tag))

    def handle_endtag(self, tag):
        start = self._pos()
        close = self.tree.text.find(">", start)
        end = len(self.tree.text) if close < 0 else close + 1
        self.tree.events.append((start, "end", tag))
        if any(el.tag == tag for el in self.stack):
            while self.stack:
                el = self.stack.pop()
                if el.tag == tag:
                    el.end_start, el.end = start, end
                    break

    def handle_data(self, data):
        self.tree.events.append((self._pos(), "data", data))

    def handle_comment(self, data):
        start = self._pos()
        self.tree.comment_spans.append((start, start + len(data) + 7))
        self.tree.events.append((start, "comment", data))

    def handle_decl(self, decl):
        self.tree.events.append((self._pos(), "decl", decl))

    def handle_pi(self, data):
        self.tree.events.append((self._pos(), "pi", data))

    def unknown_decl(self, data):
        self.tree.events.append((self._pos(), "decl", data))


def build_html(lines):
    tree = Tree(lines)
    builder = _HtmlBuilder(tree)
    builder.feed(tree.text)
    builder.close()
    return tree


def _tag_end(text, start):
    """Ende eines Start-Tags ab '<' (Anfuehrungszeichen beachten)."""
    quote, i = None, start + 1
    while i < len(text):
        ch = text[i]
        if quote:
            if ch == quote:
                quote = None
        elif ch in "'\"":
            quote = ch
        elif ch == ">":
            return i + 1
        i += 1
    raise ParseError("Tag ohne Ende (Position %d)" % start)


def build_xml(lines):
    tree = Tree(lines)
    text = tree.text
    data = text.encode("utf-8")
    if len(data) == len(text):
        char_at = lambda b: b
    else:
        offsets, b = [], 0
        for ch in text:
            offsets.append(b)
            b += len(ch.encode("utf-8"))
        char_at = lambda pos: bisect.bisect_left(offsets, pos)
    parser = xml.parsers.expat.ParserCreate()
    parser.ordered_attributes = True
    stack = []

    def start(name, attrs):
        pos = char_at(parser.CurrentByteIndex)
        end = _tag_end(text, pos)
        pairs = list(zip(attrs[0::2], attrs[1::2]))
        el = tree.add_element(name, pairs, pos, end, stack)
        tree.events.append((pos, "start", (name, pairs)))
        if text[end - 2] == "/":
            el.end = end
            el.empty = True
        stack.append(el)

    def end(name):
        el = stack.pop()
        pos = char_at(parser.CurrentByteIndex)
        if getattr(el, "empty", False):
            tree.events.append((el.end, "end", name))
            return
        el.end_start = pos
        el.end = text.find(">", pos) + 1
        tree.events.append((pos, "end", name))

    def chars(value):
        tree.events.append((char_at(parser.CurrentByteIndex), "data", value))

    def comment(value):
        pos = char_at(parser.CurrentByteIndex)
        tree.comment_spans.append((pos, pos + len(value) + 7))
        tree.events.append((pos, "comment", value))

    def pi(target, value):
        tree.events.append((char_at(parser.CurrentByteIndex), "pi", target + " " + value))

    def decl(version, encoding, standalone):
        tree.events.append((char_at(parser.CurrentByteIndex), "decl",
                            "xml %s %s %s" % (version, encoding, standalone)))

    def doctype(name, sysid, pubid, internal):
        tree.events.append((char_at(parser.CurrentByteIndex), "decl",
                            "DOCTYPE %s %s %s" % (name, sysid, pubid)))

    parser.StartElementHandler = start
    parser.EndElementHandler = end
    parser.CharacterDataHandler = chars
    parser.CommentHandler = comment
    parser.ProcessingInstructionHandler = pi
    parser.XmlDeclHandler = decl
    parser.StartDoctypeDeclHandler = doctype
    try:
        parser.Parse(data, True)
    except xml.parsers.expat.ExpatError as exc:
        raise ParseError("Zeile %s: %s" % (exc.lineno, xml.parsers.expat.ErrorString(exc.code)))
    return tree


def _q(value):
    return '"%s"' % value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


class MarkupAdapter(Adapter):
    has_units = True
    default_indent = "  "
    gap_top = 0
    gap_nested = 0
    dup_re = re.compile(r"\[\d+\]$")
    case_insensitive = True

    def build(self, lines):
        raise NotImplementedError

    # -- Normalform ------------------------------------------------------

    def _embedded(self, tag, attrs, text):
        """Inhalt von <script>/<style> ueber JS-/CSS-Normalform (sonst exakt)."""
        from . import ADAPTERS
        from ..fileio import Doc
        kind = None
        if tag == "script" and (dict(attrs).get("type") or "").lower() in JS_TYPES:
            kind = "js"
        elif tag == "style":
            kind = "css"
        if kind is None:
            return None
        adapter = ADAPTERS[kind]
        doc = Doc(text.split("\n"))
        try:
            from ..profile import complete
            return adapter.normalize(doc, complete({"mode": kind}))
        except ParseError:
            return None

    def normal_events(self, events, prof):
        out, buf, stack = [], [], []
        keep_ws = prof["whitespace"] == "keep"
        keep_comments = prof["comments"] == "keep"

        def flush():
            if not buf:
                return
            text = "".join(buf)
            del buf[:]
            tag = stack[-1][0] if stack else None
            if tag in ("script", "style") and self.case_insensitive:
                emb = self._embedded(tag, stack[-1][1], text)
                if emb is not None:
                    out.append("X " + _q(emb))
                    return
            if not keep_ws and not any(t in KEEP_SPACE for t, _ in stack):
                # lockere Stufe: Folgen -> ein Leerzeichen, Rand und reiner Leerraum egal
                text = re.sub(r"\s+", " ", text).strip()
                if not text:
                    return
            out.append("T " + _q(text))

        for _, kind, value in events:
            if kind == "data":
                buf.append(value)
                continue
            if kind == "comment" and not keep_comments:
                continue
            flush()
            if kind == "start":
                tag, attrs = value
                pairs = sorted((k, "" if v is None else v) for k, v in attrs)
                out.append("S %s %s" % (tag, " ".join("%s=%s" % (k, _q(v)) for k, v in pairs)))
                stack.append((tag, attrs))
            elif kind == "end":
                out.append("E " + value)
                for i in range(len(stack) - 1, -1, -1):
                    if stack[i][0] == value:
                        del stack[i:]
                        break
            else:
                out.append("%s %s" % (kind[0].upper(), _q(value)))
        flush()
        return "\n".join(out)

    def normalize(self, doc, prof):
        return self.normal_events(self.build(doc.lines).events, prof)

    def normalize_unit(self, doc, info, unit, prof):
        el = unit.node
        events = [e for e in info.tree.events if el.start <= e[0] < el.end]
        if prof["comments"] == "keep":
            events = [e for e in info.tree.events
                      if info.tree.starts[unit.start] <= e[0] < el.end and
                      (e[1] == "comment" or e[0] >= el.start)]
        return self.normal_events(events, prof)

    def validate(self, doc):
        self.build(doc.lines)

    # -- Einheiten -------------------------------------------------------

    def can_contain(self, unit):
        return unit.node.end_start is not None

    def analyze(self, doc):
        tree = self.build(doc.lines)
        self._paths(tree)
        comment_lines = tree.comment_lines()
        units, by_el = [], {}
        for el in tree.elements:
            if el.end is None or not tree.alone_before(el.start) or not tree.alone_after(el.end):
                continue
            parent = el.parent
            while parent is not None and parent not in by_el:
                parent = parent.parent
            unit_parent = by_el.get(parent)
            first = tree.line(el.start)
            floor = tree.line(parent.start_end - 1) + 1 if parent is not None else 0
            while first - 1 >= floor and first - 1 in comment_lines:
                first -= 1
            unit = Unit(el.path, "element", el, first, tree.line(el.end - 1) + 1,
                        leading_ws(doc.lines[tree.line(el.start)]), unit_parent)
            by_el[el] = unit
            units.append(unit)
        return Info(doc.lines, units, tree=tree, start_line=self._start_line(tree))

    def _paths(self, tree):
        ids = Counter(dict(el.attrs).get("id") for el in tree.elements)

        def step(el):
            classes = sorted(set((dict(el.attrs).get("class") or "").split()))
            return el.tag + "".join("." + c for c in classes)

        def assign(elements, prefix):
            steps = [step(el) for el in elements]
            counts, seen = Counter(steps), Counter()
            for el, name in zip(elements, steps):
                seen[name] += 1
                ident = dict(el.attrs).get("id")
                if ident and ids[ident] == 1 and not re.search(r"[\s/\[\]]", ident):
                    el.path = "#" + ident
                else:
                    label = name if counts[name] == 1 else "%s[%d]" % (name, seen[name])
                    el.path = prefix + "/" + label if prefix else label
                assign(el.children, el.path)

        assign(tree.roots, "")

    def _start_line(self, tree):
        line = 0
        for offset, kind, value in tree.events:
            if kind in ("decl", "pi", "comment") or (kind == "data" and not value.strip()):
                continue
            line = tree.line(offset)
            break
        return line

    def _body(self, unit, info):
        el, tree = unit.node, info.tree
        if el.end_start is None or not tree.alone_after(el.start_end) or \
                not tree.alone_before(el.end_start):
            return None
        children = [c for c in el.children if c.start >= el.start_end]
        if children:
            indent = leading_ws(info.lines[tree.line(children[0].start)])
        else:
            indent = unit.indent + (self.detect_unit(None, info) or self.default_indent)
        return tree.line(el.start_end - 1) + 1, tree.line(el.end_start), indent

    def position(self, info, pos):
        return self.simple_position(info, pos, lambda unit: self._body(unit, info))

    def detect_unit(self, doc, info=None):
        try:
            info = info or self.analyze(doc)
        except ParseError:
            return detect_generic(doc.lines)
        tree, diffs = info.tree, Counter()
        for el in tree.elements:
            for child in el.children[:1]:
                if tree.line(child.start) > tree.line(el.start) and tree.alone_before(child.start) \
                        and tree.alone_before(el.start):
                    outer = leading_ws(info.lines[tree.line(el.start)])
                    ws = leading_ws(info.lines[tree.line(child.start)])
                    if ws.startswith(outer) and len(ws) > len(outer):
                        diffs[ws[len(outer):]] += 1
        return diffs.most_common(1)[0][0] if diffs else detect_generic(info.lines)


class HtmlAdapter(MarkupAdapter):
    mode = "html"
    label = "HTML"

    def build(self, lines):
        return build_html(lines)


class XmlAdapter(MarkupAdapter):
    mode = "xml"
    label = "XML"
    case_insensitive = False

    def build(self, lines):
        return build_xml(lines)
