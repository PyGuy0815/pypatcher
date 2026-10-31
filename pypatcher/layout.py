"""Relative Einrueckung im Patch (manifest.txt Abschnitt 6.1).

Im Patch steht jede fuehrende Tab-Stelle fuer eine Ebene relativ zur
Grundtiefe der Zielstelle. Grundtiefe und Ebenen-Einheit kommen aus der
Zieldatei; "layout" ist ein optionaler Check.
"""

from .errors import PatchInvalid


def leading_ws(line):
    return line[:len(line) - len(line.lstrip(" \t"))]


def levels(line):
    return len(line) - len(line.lstrip("\t"))


def describe(unit):
    if unit is None:
        return "unbekannt"
    if unit == "\t":
        return "Tab"
    if unit.strip(" ") == "":
        return "%d Leerzeichen" % len(unit)
    return repr(unit)


def unit_from_layout(layout):
    if not layout or "indent" not in layout:
        return None
    if layout["indent"] == "tabs":
        return "\t"
    if layout["indent"] == "spaces":
        width = layout.get("width")
        if not isinstance(width, int) or isinstance(width, bool) or width < 1:
            raise PatchInvalid("layout: width muss eine positive Zahl sein")
        return " " * width
    raise PatchInvalid("layout: indent muss 'tabs' oder 'spaces' sein")


def layout_for(unit):
    if unit == "\t":
        return {"indent": "tabs"}
    return {"indent": "spaces", "width": len(unit)}


def to_abs(rel_lines, base, unit):
    """Patch -> Datei: Grundtiefe + Ebenen x Einheit + Rest woertlich."""
    out = []
    for line in rel_lines:
        if line == "":
            out.append("")
            continue
        n = levels(line)
        if n and unit is None:
            raise PatchInvalid("Einrueckungseinheit nicht bestimmbar - layout angeben")
        out.append(base + (unit or "") * n + line[n:])
    return out


def to_rel(abs_lines, base, unit):
    """Datei -> Patch; None, wenn nicht umkehrbar eindeutig darstellbar."""
    out = []
    for line in abs_lines:
        rel = rel_line(line, base, unit)
        if rel is None:
            return None
        out.append(rel)
    if to_abs(out, base, unit) != list(abs_lines):
        return None
    return out


def canon_rel(line, unit):
    """Zeile aus dem Patch in die Tab-Form: fuehrende Leerzeichen, die ein
    Vielfaches der Ebenen-Einheit der Datei sind, zaehlen wie Tabs (so wie
    rel_line es fuer die Datei macht) - "    x" == "\tx" bei 4 Leerzeichen."""
    if not unit or unit == "\t" or not line.strip():
        return line
    n = levels(line)
    rest = line[n:]
    while rest.startswith(unit):
        rest = rest[len(unit):]
        n += 1
    return "\t" * n + rest


def rel_line(line, base, unit, blank_as_empty=False):
    if line == "" or (blank_as_empty and not line.strip()):
        return ""
    if not line.startswith(base):
        return None
    rest = line[len(base):]
    n = 0
    if unit:
        while rest.startswith(unit):
            rest = rest[len(unit):]
            n += 1
    if rest.startswith("\t"):
        return None
    return "\t" * n + rest


def detect_generic(lines):
    """Ebenen-Einheit aus beliebigem Text: Tab oder kleinste Leerzeichen-Tiefe."""
    tabs = 0
    widths = []
    for line in lines:
        if not line.strip():
            continue
        ws = leading_ws(line)
        if ws.startswith("\t"):
            tabs += 1
        elif ws:
            n = len(ws) - len(ws.lstrip(" "))
            if n:
                widths.append(n)
    if tabs and tabs >= len(widths):
        return "\t"
    if widths:
        return " " * min(widths)
    return None
