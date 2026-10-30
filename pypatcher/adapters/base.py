"""Gemeinsame Grundlage der Adapter (manifest.txt Abschnitt 8)."""

import difflib
import re

from ..errors import Conflict, PatchInvalid
from ..layout import detect_generic


class ParseError(Exception):
    """Datei bzw. Ergebnis ist im jeweiligen Format nicht gueltig."""


class Unit(object):
    """Einheit: ganze Zeilen [start, end) inkl. Kommentaren direkt darueber."""

    def __init__(self, path, kind, node, start, end, indent, parent):
        self.path = path
        self.kind = kind
        self.node = node
        self.start = start
        self.end = end
        self.indent = indent
        self.parent = parent


class Info(object):
    """Analyse einer Datei: Zeilen, Einheiten (Eltern vor Kindern) und
    adapterspezifische Daten in extra."""

    def __init__(self, lines, units, **extra):
        self.lines = lines
        self.units = units
        self.__dict__.update(extra)


class Adapter(object):
    """Schnittstelle; Formate ohne Einheiten (text) nutzen nur die oberen Teile."""

    mode = None
    label = "Text"
    has_units = False
    default_indent = "\t"
    gap_top = 1                 # Leerzeilen um eingefuegte Einheiten (oberste Ebene)
    gap_nested = 1              # ... innerhalb von Bloecken
    dup_re = re.compile(r"#\d+$")   # Kennzeichen fuer gleichnamige Einheiten

    def normalize(self, doc, prof):
        raise NotImplementedError

    def normalize_unit(self, doc, info, unit, prof):
        raise NotImplementedError

    def validate(self, doc):
        pass

    def simple(self, line):
        """Gehoert die Zeile zu einer kleinen Einheit, zwischen denen beim
        Einfuegen kein Abstand steht (Python: Zuweisungen/Imports)?"""
        return False

    def detect_unit(self, doc, info=None):
        return detect_generic(doc.lines)

    # -- Einheiten ------------------------------------------------------

    def analyze(self, doc):
        raise NotImplementedError

    def find(self, info, path):
        for unit in info.units:
            if unit.path == path:
                return unit
        dups = [u.path for u in info.units if u.path.startswith(path)
                and self.dup_re.match(u.path[len(path):])]
        if dups:
            raise Conflict("Einheit %r ist nicht eindeutig: %s" % (path, ", ".join(dups)))
        raise Conflict("Einheit %r nicht gefunden%s" % (path, self.suggest(info, path)))

    def suggest(self, info, path):
        """Konfliktanzeige (R006): aehnliche bzw. vorhandene Einheiten."""
        names = [u.path for u in info.units if u.kind != "header"]
        close = difflib.get_close_matches(path, names, n=3, cutoff=0.6)
        if close:
            return " - aehnlich: " + ", ".join(close)
        if not names:
            return " - die Datei hat keine Einheiten"
        return " - vorhanden: " + ", ".join(names[:12]) + (" ..." if len(names) > 12 else "")

    def at_span(self, info, start, end):
        for unit in info.units:
            if unit.kind != "header" and unit.start == start and unit.end == end:
                return unit
        return None

    def position(self, info, pos):
        raise NotImplementedError

    def can_contain(self, unit):
        """Kann die Einheit weitere Einheiten aufnehmen (start-of:/end-of:)?"""
        return False

    def opens_block(self, line):
        """Zeile oeffnet einen Block (danach keine Leerzeile einfuegen)."""
        return False

    def closes_block(self, line):
        """Zeile schliesst einen Block (davor keine Leerzeile einfuegen)."""
        return False

    def rename(self, rel_lines, name):
        raise PatchInvalid("as= wird bei %s nicht unterstuetzt" % self.label)

    def repair(self, doc):
        """Nach Einfuegen/Loeschen/Ersetzen von Einheiten: formatbedingte
        Trennzeichen richten (JSON: Kommas). Standard: nichts aendern."""
        return doc

    # -- gemeinsame Hilfen ----------------------------------------------

    @staticmethod
    def label_dups(names):
        """Gleichnamige Geschwister durchnummerieren: [a, b, a] -> [a#1, b, a#2]."""
        counts = {}
        for name in names:
            counts[name] = counts.get(name, 0) + 1
        seen, out = {}, []
        for name in names:
            seen[name] = seen.get(name, 0) + 1
            out.append(name if counts[name] == 1 else "%s#%d" % (name, seen[name]))
        return out

    def simple_position(self, info, pos, body_range):
        """Positionen fuer Formate mit Klammer-Bloecken (JS, CSS, HTML/XML).

        body_range(unit) -> (erste Rumpfzeile, Zeile des Blockendes, Rumpf-
        Einrueckung) oder None, wenn der Rumpf nicht zeilenweise abgrenzbar ist.
        """
        lines = info.lines
        if pos == "start":
            return getattr(info, "start_line", 0), "", True
        if pos == "end":
            idx = len(lines)
            while idx > 0 and not lines[idx - 1].strip():
                idx -= 1
            return idx, "", True
        kind, _, target = pos.partition(":")
        if not target or kind not in ("after", "before", "start-of", "end-of"):
            raise PatchInvalid("ungueltige Position %r" % pos)
        unit = self.find(info, target)
        if kind == "after":
            return unit.end, unit.indent, unit.parent is None
        if kind == "before":
            return unit.start, unit.indent, unit.parent is None
        if not self.can_contain(unit):
            raise PatchInvalid("%s: %r kann keine Einheiten aufnehmen" % (kind, target))
        body = body_range(unit)
        if body is None:
            raise PatchInvalid("%s: Rumpf von %r ist nicht zeilenweise abgrenzbar" % (kind, target))
        first, last, indent = body
        return (first if kind == "start-of" else last), indent, False
