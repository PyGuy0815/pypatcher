"""Ausfuehrung eines Patches im Speicher (manifest.txt Abschnitte 4.5, 7, 12).

Derselbe Ablauf dient zum Versiegeln (seal: Hashes und Altinhalte werden
eingetragen) und zum Pruefen/Anwenden (check/apply: alles wird verglichen).
Geschrieben wird hier nichts - das erledigt journal.commit_apply.
"""

import base64
import binascii
import os
import re

from . import adapters, fileio, layout as lay, profile as pf
from .adapters import PY, ParseError
from .errors import Conflict, PatchInvalid
from .patchfile import hash_match, norm_path

RENAME_RE = re.compile(r"^(\t*(?:async[ \t]+)?(?:def|class)[ \t]+)([A-Za-z_]\w*)")


class Ctx(object):
    def __init__(self, root, seal=False, match="file", exact=False, allow_mixed=False,
                 config=None, no_layout=False, source_dir=None):
        self.root = root
        self.seal = seal
        self.match = match
        self.exact = exact
        self.allow_mixed = allow_mixed
        self.config = config or {}
        self.no_layout = no_layout
        self.source_dir = source_dir or root
        self.docs = {}        # pfad -> Doc oder None (existiert nicht)
        self.profiles = {}
        self.originals = {}   # Stand vor dem Patch
        self.hints = []
        self.moves = set()

    def abspath(self, rel):
        return os.path.join(self.root, *rel.split("/"))

    def hint(self, msg):
        self.hints.append(msg)

    def get(self, rel, prof):
        if rel not in self.docs:
            path = self.abspath(rel)
            doc = fileio.load(path, rel, prof, self.allow_mixed) if os.path.isfile(path) else None
            self.docs[rel] = self.originals[rel] = doc
            self.profiles[rel] = prof
        return self.docs[rel]

    def set(self, rel, doc, prof):
        self.get(rel, prof)
        self.docs[rel] = doc
        self.profiles[rel] = prof

    def touched(self):
        return [rel for rel, doc in self.docs.items() if doc is not self.originals[rel]]

    def expect(self, obj, key, value, what, level="file"):
        """seal: Wert eintragen. Sonst: Wert pruefen, sofern der Patch ihn enthaelt
        (welche Pruefungen enthalten sind, bestimmt die Pruefstufe beim Erzeugen)."""
        if self.seal:
            obj[key] = value
            return
        if level == "file" and self.match == "unit":
            return
        if level == "text" and not self.exact:
            return
        stored = obj.get(key)
        if stored is None:
            return
        same = stored == value if key in ("unit", "unit_after") else hash_match(stored, value)
        if not same:
            raise Conflict("%s: %s stimmt nicht\n    erwartet: %s\n    gefunden: %s"
                           % (what, key, stored, value))


class FileState(object):
    """Einrueckungs-Einheit einer Datei: erkannt, layout als Check."""

    def __init__(self, ctx, entry, prof, unit):
        self.ctx = ctx
        self.entry = entry
        self.prof = prof
        self.unit = unit

    def need(self, lines, what):
        if self.unit is None and any(lay.levels(l) for l in lines or ()):
            if not self.ctx.seal:
                raise PatchInvalid("%s: Einrueckungseinheit nicht bestimmbar - layout angeben" % what)
            self.unit = "    " if self.prof["mode"] == "python" else "\t"
            self.entry["layout"] = dict(self.entry.get("layout") or {}, **lay.layout_for(self.unit))
            self.ctx.hint("%s: Einrueckung nicht aus der Datei ableitbar - layout %s angenommen"
                          % (what, lay.describe(self.unit)))
        return self.unit

    def literal(self, ch):
        return bool(ch.get("literal", (self.entry.get("layout") or {}).get("lines") == "literal"))


# -- Hilfen ---------------------------------------------------------------

def _hash(doc, prof, what, result=False):
    try:
        return adapters.file_hash(doc, prof)
    except ParseError as exc:
        if result:
            raise PatchInvalid("%s: Ergebnis ist kein gueltiges Python (%s)" % (what, exc))
        raise Conflict("%s: Datei ist kein gueltiges Python (%s)" % (what, exc))


def _chain_hash(doc, prof):
    """Datei-Zwischenstand (c=): Funktions-Hash, bei ungueltigem Zwischenstand
    der Text-Hash - auf beiden Seiten eindeutig gleich."""
    try:
        return adapters.file_hash(doc, prof)
    except ParseError:
        return adapters.text_hash(doc)


def _info(doc, what, result=False):
    try:
        return PY.analyze(doc)
    except ParseError as exc:
        if result:
            raise PatchInvalid("%s: Ergebnis ist kein gueltiges Python (%s)" % (what, exc))
        raise Conflict("%s: Datei ist kein gueltiges Python (%s)" % (what, exc))


def _find(info, path, what):
    if not isinstance(path, str) or not path:
        raise PatchInvalid("%s: unit fehlt" % what)
    try:
        return PY.find(info, path)
    except Conflict as exc:
        raise Conflict("%s: %s" % (what, exc))


def _position(info, ch, what):
    pos = ch.get("position")
    if not isinstance(pos, str):
        raise PatchInvalid("%s: position fehlt" % what)
    try:
        return PY.position(info, pos)
    except (Conflict, PatchInvalid) as exc:
        raise type(exc)("%s: %s" % (what, exc))


def _require_py(prof, what):
    if prof["mode"] != "python":
        raise PatchInvalid("%s: Einheiten gibt es nur bei mode python" % what)


def _lines(ch, key, what):
    lines = ch.get(key)
    if not isinstance(lines, list) or not all(isinstance(x, str) for x in lines):
        raise PatchInvalid("%s: %s muss eine Liste von Zeilen sein" % (what, key))
    return lines


def _abs(ch, key, base, fs, what, lit):
    lines = _lines(ch, key, what)
    if lit:
        return list(lines)
    return lay.to_abs(lines, base, fs.need(lines, what))


def _blank_run(lines):
    n = 0
    for line in lines:
        if line.strip():
            break
        n += 1
    return n


def _insert(doc, idx, block, top):
    """Block einfuegen und den ueblichen Abstand einhalten (Python: 2 bzw. 1 Leerzeile)."""
    lines = doc.lines
    gap = 2 if top else 1
    before, after = lines[:idx], lines[idx:]
    pre = _blank_run(reversed(before))
    post = _blank_run(after)
    add_pre = add_post = 0
    if any(l.strip() for l in before):
        prev = before[len(before) - pre - 1]
        if not prev.rstrip().endswith(":"):
            add_pre = max(0, gap - pre)
    if any(l.strip() for l in after):
        nxt = after[post]
        add_post = max(0, (2 if not lay.leading_ws(nxt) else gap) - post)
    new = before + [""] * add_pre + list(block) + [""] * add_post + after
    return doc.with_lines(new), idx + add_pre


def _remove(doc, start, end):
    """Block entfernen; der groessere der beiden Abstaende bleibt erhalten."""
    lines = doc.lines[:start] + doc.lines[end:]
    pre = _blank_run(reversed(lines[:start]))
    post = _blank_run(lines[start:])
    head, tail = lines[:start - pre], lines[start + post:]
    if not any(l.strip() for l in tail):
        return doc.with_lines(head)
    if not any(l.strip() for l in head):
        return doc.with_lines(tail)
    gap = 0 if head[-1].rstrip().endswith(":") else max(pre, post)
    return doc.with_lines(head + [""] * gap + tail)


def _unit_after(ctx, ch, nd, start, end, prof, what, key="unit_after"):
    info = _info(nd, what, result=True)
    unit = PY.at_span(info, start, end)
    if unit is None:
        raise PatchInvalid("%s: der neue Block ist keine einzelne Funktion/Klasse" % what)
    if ctx.seal and key == "unit" and ch.get("unit") not in (None, unit.path):
        ctx.hint("%s: unit %r korrigiert zu %r" % (what, ch.get("unit"), unit.path))
    ctx.expect(ch, key, unit.path, what, "unit")
    ctx.expect(ch, "unit_hash_after", adapters.unit_hash(nd, info, unit, prof), what, "unit")


# -- Einheiten ------------------------------------------------------------

def op_replace_unit(ctx, path, doc, ch, prof, fs, what):
    _require_py(prof, what)
    info = _info(doc, what)
    unit = _find(info, ch.get("unit"), what)
    ctx.expect(ch, "unit_hash_before", adapters.unit_hash(doc, info, unit, prof), what, "unit")
    new = _abs(ch, "new", unit.indent, fs, what, fs.literal(ch))
    nd = doc.with_lines(doc.lines[:unit.start] + new + doc.lines[unit.end:])
    if unit.kind == "header":
        info2 = _info(nd, what, result=True)
        if info2.units[0].end != len(new):
            raise PatchInvalid("%s: new bildet keinen vollstaendigen Datei-Kopf" % what)
        ctx.expect(ch, "unit_hash_after",
                   adapters.unit_hash(nd, info2, info2.units[0], prof), what, "unit")
        return nd
    _unit_after(ctx, ch, nd, unit.start, unit.start + len(new), prof, what)
    return nd


def _place_unit(ctx, doc, ch, prof, fs, what):
    info = _info(doc, what)
    idx, base, top = _position(info, ch, what)
    new = _abs(ch, "new", base, fs, what, fs.literal(ch))
    if not new:
        raise PatchInvalid("%s: new ist leer" % what)
    nd, start = _insert(doc, idx, new, top)
    _unit_after(ctx, ch, nd, start, start + len(new), prof, what, key="unit")
    return nd


def op_add_unit(ctx, path, doc, ch, prof, fs, what):
    _require_py(prof, what)
    return _place_unit(ctx, doc, ch, prof, fs, what)


def op_delete_unit(ctx, path, doc, ch, prof, fs, what):
    _require_py(prof, what)
    info = _info(doc, what)
    unit = _find(info, ch.get("unit"), what)
    ctx.expect(ch, "unit_hash_before", adapters.unit_hash(doc, info, unit, prof), what, "unit")
    return _remove(doc, unit.start, unit.end)


def _split_from(value, path, what):
    if not isinstance(value, str) or not value:
        raise PatchInvalid("%s: from fehlt" % what)
    if "::" in value:
        src, unit = value.split("::", 1)
        return norm_path(src, what + ": from"), unit
    return path, value


def _rename(rel, name, what):
    if not re.match(r"^[A-Za-z_]\w*$", name or ""):
        raise PatchInvalid("%s: as=%r ist kein gueltiger Name" % (what, name))
    for i, line in enumerate(rel):
        match = RENAME_RE.match(line)
        if match:
            return rel[:i] + [match.group(1) + name + line[match.end(2):]] + rel[i + 1:]
    raise PatchInvalid("%s: keine def/class-Zeile zum Umbenennen gefunden" % what)


def op_copy_unit(ctx, path, doc, ch, prof, fs, what):
    _require_py(prof, what)
    src_path, src_name = _split_from(ch.get("from"), path, what)
    if src_path == path:
        sdoc, sprof = doc, prof
    else:
        sprof = ctx.profiles.get(src_path) or pf.resolve(src_path)
        _require_py(sprof, what + " (Quelle)")
        sdoc = ctx.get(src_path, sprof)
        if sdoc is None:
            raise Conflict("%s: Quelldatei %s existiert nicht" % (what, src_path))
    sinfo = _info(sdoc, what + " (Quelle)")
    src = _find(sinfo, src_name, what)
    if src.kind == "header":
        raise PatchInvalid("%s: @header kann nicht kopiert werden" % what)
    ctx.expect(ch, "source_unit_hash", adapters.unit_hash(sdoc, sinfo, src, sprof), what, "unit")
    # Inhalt immer aus der Quelle (durch source_unit_hash gesichert), nicht aus dem Patch
    sunit = fs.unit if src_path == path else PY.detect_unit(sdoc, sinfo)
    rel = lay.to_rel(sdoc.lines[src.start:src.end], src.indent, sunit)
    if rel is None:
        raise Conflict("%s: Quelle ist nicht relativ darstellbar" % what)
    ch["new"] = _rename(rel, ch["as"], what) if ch.get("as") else rel
    ch.pop("literal", None)
    return _place_unit(ctx, doc, ch, prof, fs, what)


def op_move_unit(ctx, path, doc, ch, prof, fs, what):
    _require_py(prof, what)
    if "from" in ch:                      # Ziel-Haelfte: einfuegen
        nd = op_copy_unit(ctx, path, doc, ch, prof, fs, what)
        ctx.moves.add(ch["move_id"])
        return nd
    if "to" in ch:                        # Quell-Haelfte: entfernen
        if ch["move_id"] not in ctx.moves:
            raise PatchInvalid("%s: der 'from'-Teil von move_id %r muss vorher ausgefuehrt "
                               "werden" % (what, ch["move_id"]))
        return op_delete_unit(ctx, path, doc, ch, prof, fs, what)
    info = _info(doc, what)
    unit = _find(info, ch.get("unit"), what)
    if unit.kind == "header":
        raise PatchInvalid("%s: @header kann nicht verschoben werden" % what)
    ctx.expect(ch, "unit_hash_before", adapters.unit_hash(doc, info, unit, prof), what, "unit")
    block = doc.lines[unit.start:unit.end]
    rel = lay.to_rel(block, unit.indent, fs.unit)
    if rel is None:
        raise Conflict("%s: Einheit ist nicht relativ darstellbar - move nicht moeglich" % what)
    nd1 = _remove(doc, unit.start, unit.end)
    idx, base, top = _position(_info(nd1, what, result=True), ch, what)
    new = lay.to_abs(rel, base, fs.unit)
    nd, start = _insert(nd1, idx, new, top)
    _unit_after(ctx, ch, nd, start, start + len(new), prof, what)
    return nd


# -- Text -----------------------------------------------------------------

def _scope(ctx, doc, ch, prof, what):
    name = ch.get("scope")
    if not name:
        return None, 0, len(doc.lines)
    _require_py(prof, what)
    info = _info(doc, what)
    unit = _find(info, name, what)
    ctx.expect(ch, "unit_hash_before", adapters.unit_hash(doc, info, unit, prof), what, "unit")
    return unit, unit.start, unit.end


def _scope_after(ctx, nd, ch, prof, unit, delta, what):
    if unit is None:
        return
    info = _info(nd, what, result=True)
    if unit.kind == "header":
        after = info.units[0]
    else:
        after = PY.at_span(info, unit.start, unit.end + delta)
    if after is None:
        raise PatchInvalid("%s: Einheit %s ist nach der Aenderung nicht mehr abgrenzbar"
                           % (what, unit.path))
    ctx.expect(ch, "unit_hash_after", adapters.unit_hash(nd, info, after, prof), what, "unit")


def _match(doc, ch, key, lo, hi, prof, fs, what):
    """Block eindeutig finden: zeilenweise, relative Einrueckung exakt."""
    pattern = _lines(ch, key, what)
    ref = next((i for i, l in enumerate(pattern) if l.strip()), None)
    if ref is None:
        raise PatchInvalid("%s: %s ist leer" % (what, key))
    lit = fs.literal(ch)
    unit = None if lit else fs.need(pattern, what)
    lvl = 0 if lit else lay.levels(pattern[ref])
    ignore_ws = prof.get("whitespace") == "ignore"
    norm = (lambda s: s.rstrip(" \t")) if ignore_ws else (lambda s: s)
    want = [norm(l) for l in pattern]
    hits = []
    for i in range(lo, hi - len(pattern) + 1):
        cand = doc.lines[i:i + len(pattern)]
        base = ""
        if lit:
            rel = cand
        else:
            ws = lay.leading_ws(cand[ref])
            suffix = (unit or "") * lvl
            if not ws.endswith(suffix):
                continue
            base = ws[:len(ws) - len(suffix)]
            rel = [lay.rel_line(l, base, unit, blank_as_empty=ignore_ws) for l in cand]
            if None in rel:
                continue
        if [norm(l) for l in rel] == want:
            hits.append((i, base))
    if not hits:
        raise Conflict("%s: %s-Block nicht gefunden" % (what, key))
    if len(hits) > 1:
        raise Conflict("%s: %s-Block nicht eindeutig (%d Treffer, Zeilen %s)"
                       % (what, key, len(hits), ", ".join(str(h[0] + 1) for h in hits)))
    return hits[0][0], hits[0][1], lit


def op_replace_text(ctx, path, doc, ch, prof, fs, what):
    unit, lo, hi = _scope(ctx, doc, ch, prof, what)
    idx, base, lit = _match(doc, ch, "old", lo, hi, prof, fs, what)
    k = len(ch["old"])
    new = _abs(ch, "new", base, fs, what, lit)
    nd = doc.with_lines(doc.lines[:idx] + new + doc.lines[idx + k:])
    _scope_after(ctx, nd, ch, prof, unit, len(new) - k, what)
    return nd


def op_insert_text(ctx, path, doc, ch, prof, fs, what):
    unit, lo, hi = _scope(ctx, doc, ch, prof, what)
    idx, base, lit = _match(doc, ch, "anchor", lo, hi, prof, fs, what)
    where = ch.get("position")
    if where not in ("before", "after"):
        raise PatchInvalid("%s: position muss 'before' oder 'after' sein" % what)
    if where == "after":
        idx += len(ch["anchor"])
    new = _abs(ch, "new", base, fs, what, lit)
    nd = doc.with_lines(doc.lines[:idx] + new + doc.lines[idx:])
    _scope_after(ctx, nd, ch, prof, unit, len(new), what)
    return nd


def op_delete_text(ctx, path, doc, ch, prof, fs, what):
    unit, lo, hi = _scope(ctx, doc, ch, prof, what)
    idx, _, _ = _match(doc, ch, "old", lo, hi, prof, fs, what)
    k = len(ch["old"])
    nd = doc.with_lines(doc.lines[:idx] + doc.lines[idx + k:])
    _scope_after(ctx, nd, ch, prof, unit, -k, what)
    return nd


OPS = {
    "replace_unit": op_replace_unit,
    "add_unit": op_add_unit,
    "delete_unit": op_delete_unit,
    "copy_unit": op_copy_unit,
    "move_unit": op_move_unit,
    "replace_text": op_replace_text,
    "insert_text": op_insert_text,
    "delete_text": op_delete_text,
}


# -- Dateiaktionen --------------------------------------------------------

def _validate(doc, prof, what):
    if prof["mode"] == "python":
        try:
            PY.validate(doc)
        except ParseError as exc:
            raise PatchInvalid("%s: Ergebnis ist kein gueltiges Python (%s)" % (what, exc))


def _existing(ctx, path, prof, what):
    doc = ctx.get(path, prof)
    if doc is None:
        raise Conflict("%s: Datei existiert nicht" % what)
    return doc


def _check_before(ctx, entry, doc, prof, what):
    ctx.expect(entry, "hash_before", _hash(doc, prof, what), what)
    text = adapters.text_hash(doc)
    stored = entry.get("text_hash_before")
    if ctx.exact and not stored and not ctx.seal:
        ctx.hint("%s: --exact ohne Text-Hash im Patch (Pruefstufe full) - nicht geprueft" % what)
    if not ctx.seal and stored and not hash_match(stored, text) and not ctx.exact:
        ctx.hint("%s: kosmetische Abweichung (Kommentare/Leerraum) - wird angewendet" % what)
    ctx.expect(entry, "text_hash_before", text, what, "text")


def _check_after(ctx, entry, doc, prof, what):
    ctx.expect(entry, "hash_after", _hash(doc, prof, what, result=True), what)
    ctx.expect(entry, "text_hash_after", adapters.text_hash(doc), what, "text")


def _content_repr(ctx, entry, doc, prof, what):
    """seal: Dateiinhalt fuer den Patch (relativ, sonst literal; raw: base64)."""
    if doc.is_raw:
        return {"base64": base64.b64encode(doc.raw).decode("ascii")}
    unit = lay.unit_from_layout(entry.get("layout"))
    if unit is None:
        adapter = adapters.get(prof["mode"])
        try:
            unit = adapter.detect_unit(doc)
        except ParseError:
            unit = lay.detect_generic(doc.lines)
    rel = None if entry.get("literal") else lay.to_rel(doc.lines, "", unit)
    if rel is None:
        entry["literal"] = True
        return list(doc.lines)
    if unit and not ctx.no_layout and "layout" not in entry:
        entry["layout"] = lay.layout_for(unit)
    return rel


def _read_source(ctx, entry, prof, what):
    path = os.path.join(ctx.source_dir, entry.pop("source"))
    try:
        data = fileio.read_bytes(path)
    except OSError as exc:
        raise PatchInvalid("%s: source nicht lesbar (%s)" % (what, exc))
    if prof["mode"] == "raw":
        entry["new"] = {"base64": base64.b64encode(data).decode("ascii")}
        return
    doc = fileio.decode(data, prof["encoding"], path, ctx.allow_mixed)
    entry["new"] = _content_repr(ctx, entry, doc, prof, what)
    entry["final_newline"] = doc.final_nl
    entry.setdefault("newline", fileio.EOL_NAMES[doc.eol])


def _content_doc(ctx, entry, prof, what, like=None):
    """Doc aus entry["new"] (add/replace)."""
    if ctx.seal and "source" in entry:
        _read_source(ctx, entry, prof, what)
    new = entry.get("new")
    st_mode = like.st_mode if like is not None else None
    if prof["mode"] == "raw":
        if not isinstance(new, dict) or not isinstance(new.get("base64"), str):
            raise PatchInvalid("%s: new muss {\"base64\": ...} sein" % what)
        try:
            return fileio.Doc(raw=base64.b64decode(new["base64"], validate=True), st_mode=st_mode)
        except (binascii.Error, ValueError) as exc:
            raise PatchInvalid("%s: base64 ungueltig (%s)" % (what, exc))
    lines = _lines(entry, "new", what)
    if not entry.get("literal"):
        fs = FileState(ctx, entry, prof, lay.unit_from_layout(entry.get("layout")))
        lines = lay.to_abs(lines, "", fs.need(lines, what))
    newline = entry.get("newline")
    if newline is not None and newline not in fileio.EOL_BY_NAME:
        raise PatchInvalid("%s: newline muss lf, crlf oder cr sein" % what)
    if newline is not None:
        eol = fileio.EOL_BY_NAME[newline]
    else:
        eol = like.eol if like is not None and not like.is_raw else "\n"
    bom = like.bom if like is not None and not like.is_raw else False
    return fileio.Doc(list(lines), bool(entry.get("final_newline", True)) and bool(lines),
                      eol, bom, prof["encoding"], None, st_mode)


def act_add(ctx, entry, path, prof, what):
    if ctx.get(path, prof) is not None:
        raise Conflict("%s: Datei existiert bereits" % what)
    doc = _content_doc(ctx, entry, prof, what)
    _validate(doc, prof, what)
    _check_after(ctx, entry, doc, prof, what)
    ctx.set(path, doc, prof)


def act_delete(ctx, entry, path, prof, what):
    doc = _existing(ctx, path, prof, what)
    _check_before(ctx, entry, doc, prof, what)
    ctx.set(path, None, prof)


def act_replace(ctx, entry, path, prof, what):
    cur = _existing(ctx, path, prof, what)
    _check_before(ctx, entry, cur, prof, what)
    doc = _content_doc(ctx, entry, prof, what, like=cur)
    _validate(doc, prof, what)
    _check_after(ctx, entry, doc, prof, what)
    ctx.set(path, doc, prof)


def _move_copy(ctx, entry, path, prof, what, move):
    cur = _existing(ctx, path, prof, what)
    _check_before(ctx, entry, cur, prof, what)
    to = norm_path(entry.get("to"), what + ": to")
    if ctx.get(to, prof) is not None:
        raise Conflict("%s: Ziel %s existiert bereits" % (what, to))
    ctx.set(to, cur.copy(), prof)
    if move:
        ctx.set(path, None, prof)
    _check_after(ctx, entry, cur, prof, what)


def act_move(ctx, entry, path, prof, what):
    _move_copy(ctx, entry, path, prof, what, True)


def act_copy(ctx, entry, path, prof, what):
    _move_copy(ctx, entry, path, prof, what, False)


def act_modify(ctx, entry, path, prof, what):
    doc = _existing(ctx, path, prof, what)
    if doc.is_raw:
        raise PatchInvalid("%s: modify ist bei mode raw nicht moeglich" % what)
    _check_before(ctx, entry, doc, prof, what)
    adapter = adapters.get(prof["mode"])
    try:
        detected = adapter.detect_unit(doc)
    except ParseError as exc:
        raise Conflict("%s: Datei ist kein gueltiges Python (%s)" % (what, exc))
    wanted = lay.unit_from_layout(entry.get("layout"))
    if wanted and detected and wanted != detected:
        raise Conflict("%s: Einrueckung der Datei (%s) passt nicht zum layout (%s)"
                       % (what, lay.describe(detected), lay.describe(wanted)))
    fs = FileState(ctx, entry, prof, detected or wanted)
    if ctx.seal and fs.unit and not ctx.no_layout and not wanted:
        entry["layout"] = dict(entry.get("layout") or {}, **lay.layout_for(fs.unit))
    for i, ch in enumerate(entry["changes"], 1):
        cwhat = "%s, Aenderung %d (%s)" % (what, i, ch.get("op"))
        ctx.expect(ch, "file_hash_before", _chain_hash(doc, prof), cwhat)
        doc = OPS[ch["op"]](ctx, path, doc, ch, prof, fs, cwhat)
        ctx.expect(ch, "file_hash_after", _chain_hash(doc, prof), cwhat)
        ctx.set(path, doc, prof)
    # Syntax erst am Ende der Datei: Zwischenstaende von Textaenderungen duerfen
    # ungueltig sein (z.B. Docstring in einem Block geoeffnet, im naechsten geschlossen).
    _validate(doc, prof, what)
    _check_after(ctx, entry, doc, prof, what)
    if ctx.seal and entry["hash_before"] == entry["hash_after"]:
        ctx.hint("%s: aendert nur Kommentare/Leerraum (Funktions-Hash unveraendert)" % what)


ACTIONS = {
    "add": act_add,
    "delete": act_delete,
    "replace": act_replace,
    "move": act_move,
    "copy": act_copy,
    "modify": act_modify,
}


def _peek_binary(ctx, entry, path):
    """seal: Binaerdatei ohne Profilangabe -> mode raw."""
    if isinstance(entry.get("new"), dict):
        return True
    src = entry.get("source")
    candidate = os.path.join(ctx.source_dir, src) if src else ctx.abspath(path)
    if not os.path.isfile(candidate):
        return False
    with open(candidate, "rb") as handle:
        data = handle.read(65536)
    if b"\0" in data:
        return True
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return len(data) < 65536
    return False


def process(patch, ctx):
    """Alle Dateieintraege der Reihe nach im Speicher ausfuehren."""
    defaults = (patch.get("defaults") or {}).get("compare")
    project = ctx.config.get("compare")
    required = ctx.config.get("require")
    for n, entry in enumerate(patch["files"], 1):
        path = norm_path(entry["path"])
        what = "Datei %d (%s)" % (n, path)
        project_default = pf.lookup(project, path) if ctx.seal else None
        layer = entry.get("compare")
        if ctx.seal and not pf.has_mode(layer, defaults, project_default) and \
                _peek_binary(ctx, entry, path):
            layer = dict(layer or {}, mode="raw")
        prof = pf.resolve(path, layer, defaults, project_default)
        if ctx.seal:
            entry["compare"] = prof
        if required:
            pf.check_required(prof, path, required)
        if not ctx.seal and "hash_before" not in entry and "hash_after" not in entry:
            ctx.hint("%s: ohne Datei-Hash im Patch - nur Inhaltspruefung" % what)
        try:
            ACTIONS[entry["action"]](ctx, entry, path, prof, what)
        except (Conflict, PatchInvalid) as exc:
            exc.path = path
            raise
