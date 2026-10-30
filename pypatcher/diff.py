"""create: Patch aus zwei Staenden erzeugen (manifest.txt Abschnitt 10).

Vorgehen je Datei: Einheiten vergleichen (replace/add/delete_unit), danach
verbleibende Unterschiede als Textbloecke (replace/insert/delete_text).
Das Ergebnis wird wie bei seal im Speicher angewendet und Byte fuer Byte
mit dem neuen Stand verglichen; was nicht exakt passt, wird als ganze
Datei ersetzt. Ein erzeugter Patch fuehrt also immer genau zum neuen Stand.
"""

import copy
import difflib
import os

from . import FORMAT, __version__, adapters, engine, fileio, journal as jr
from . import layout as lay, profile as pf
from .adapters import PY, ParseError
from .errors import Conflict, PatchInvalid

MAX_ROUNDS = 5


class _Retry(Exception):
    pass


class _Probe(object):
    """Minimaler Kontext fuer die Eindeutigkeitspruefung von Textbloecken."""
    seal = True

    def hint(self, msg):
        pass


class Tree(object):
    def __init__(self, root, patterns):
        self.root = root
        self.files = jr.scan(root, patterns)
        self._data = {}

    def data(self, rel):
        if rel not in self._data:
            self._data[rel] = fileio.read_bytes(os.path.join(self.root, *rel.split("/")))
        return self._data[rel]


def _is_binary(data):
    if b"\0" in data:
        return True
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return True
    return False


def _lines(info, unit):
    return info.lines[unit.start:unit.end]


def _rel(lines, base, unit):
    """Relativ, sonst (None) -> literal."""
    return lay.to_rel(lines, base, unit)


# -- Einheiten --------------------------------------------------------------

def _children(info, parent):
    return [u for u in info.units if u.parent is parent and u.kind != "header"]


def _skeleton(info, unit):
    """Nicht-leere Zeilen der Einheit ohne die direkten Kind-Einheiten."""
    inner = set()
    for child in _children(info, unit):
        inner.update(range(child.start, child.end))
    return [info.lines[i] for i in range(unit.start, unit.end)
            if i not in inner and info.lines[i].strip()]


class UnitDiff(object):
    """Einheiten-Unterschiede einer Python-Datei."""

    def __init__(self, oinfo, ninfo, unit_old, unit_new):
        self.oi = oinfo
        self.ni = ninfo
        self.uo = unit_old
        self.un = unit_new
        self.replaces = []   # (pfad, neue Einheit)
        self.deletes = []    # alte Einheit
        self.adds = []       # (neue Einheit, position)

    def run(self):
        head_o, head_n = self.oi.units[0], self.ni.units[0]
        if _lines(self.oi, head_o) != _lines(self.ni, head_n):
            self.replaces.append(("@header", head_n))
        result = self._compare(None, None)
        if result is None:
            return False
        self.replaces.extend(result[0])
        self.deletes.extend(result[1])
        self.adds.extend(result[2])
        return True

    def _compare(self, parent_old, parent_new):
        """-> (replaces, deletes, adds) oder None (Reihenfolge geaendert)."""
        oc = _children(self.oi, parent_old)
        nc = _children(self.ni, parent_new)
        omap = dict((u.path, u) for u in oc)
        nmap = dict((u.path, u) for u in nc)
        if [u.path for u in oc if u.path in nmap] != [u.path for u in nc if u.path in omap]:
            return None
        replaces, deletes, adds = [], [], []
        for nu in nc:
            ou = omap.get(nu.path)
            if ou is None or _lines(self.oi, ou) == _lines(self.ni, nu):
                continue
            if ou.kind == nu.kind and _skeleton(self.oi, ou) == _skeleton(self.ni, nu):
                sub = self._compare(ou, nu)
                if sub is not None:
                    replaces.extend(sub[0])
                    deletes.extend(sub[1])
                    adds.extend(sub[2])
                    continue
            replaces.append((nu.path, nu))
        deletes.extend(u for u in oc if u.path not in nmap)
        for i, nu in enumerate(nc):
            if nu.path in omap:
                continue
            position = self._position(nc, i, omap, parent_new)
            if position is None:
                return None
            adds.append((nu, position))
        return replaces, deletes, adds

    @staticmethod
    def _position(siblings, i, omap, parent):
        if i > 0:
            return "after:" + siblings[i - 1].path
        following = [u for u in siblings[i + 1:] if u.path in omap]
        if following:
            return "before:" + following[0].path
        if parent is None:
            return "end"
        if parent.kind == "class":
            return "start-of:" + parent.path
        return None


# -- Textbloecke ------------------------------------------------------------

def _unique(lines, ch, key, prof, unit):
    probe = engine.FileState(_Probe(), {}, prof, unit)
    doc = fileio.Doc(list(lines))
    try:
        engine._match(doc, ch, key, 0, len(lines), prof, probe, "create")
        return True
    except (Conflict, PatchInvalid):
        return False


def _with_lines(ch, keys, blocks, unit):
    """Bloecke relativ eintragen (Grundtiefe = erste nicht-leere Zeile), sonst literal."""
    ref = next((l for block in blocks for l in block if l.strip()), "")
    base = lay.leading_ws(ref)
    rels = [_rel(block, base, unit) for block in blocks]
    if any(r is None for r in rels):
        ch["literal"] = True
        rels = [list(block) for block in blocks]
    for key, rel in zip(keys, rels):
        ch[key] = rel
    return ch


def _hunk(cur, i1, i2, new_block, prof, unit):
    """Aenderung cur[i1:i2] -> new_block mit so viel Kontext wie fuer Eindeutigkeit noetig."""
    before = after = 0
    while True:
        a, b = i1 - before, i2 + after
        old = cur[a:b]
        ctx_b, ctx_a = cur[a:i1], cur[i2:b]
        if any(l.strip() for l in old):
            if i1 == i2 and (before == 0) != (after == 0):
                anchor = ctx_b or ctx_a
                ch = _with_lines({"op": "insert_text",
                                  "position": "after" if ctx_b else "before"},
                                 ("anchor", "new"), (anchor, new_block), unit)
                if _unique(cur, ch, "anchor", prof, unit):
                    return ch
            elif not new_block and before == 0 and after == 0:
                ch = _with_lines({"op": "delete_text"}, ("old",), (old,), unit)
                if _unique(cur, ch, "old", prof, unit):
                    return ch
            else:
                ch = _with_lines({"op": "replace_text"}, ("old", "new"),
                                 (old, ctx_b + list(new_block) + ctx_a), unit)
                if _unique(cur, ch, "old", prof, unit):
                    return ch
        if a == 0 and b == len(cur):
            return None
        if (before <= after and a > 0) or b == len(cur):
            before += 1
        else:
            after += 1


def text_changes(cur_lines, new_lines, prof, unit):
    """Textbloecke von unten nach oben; None, wenn nicht darstellbar."""
    cur = list(cur_lines)
    changes = []
    matcher = difflib.SequenceMatcher(None, cur, list(new_lines), autojunk=False)
    for tag, i1, i2, j1, j2 in reversed(matcher.get_opcodes()):
        if tag == "equal":
            continue
        ch = _hunk(cur, i1, i2, new_lines[j1:j2], prof, unit)
        if ch is None:
            return None
        changes.append(ch)
        cur[i1:i2] = new_lines[j1:j2]
    return changes


# -- Hauptablauf --------------------------------------------------------------

class Creator(object):
    def __init__(self, old_root, new_root, level="unit", compare=None, include_cosmetic=False,
                 no_layout=False, config=None, allow_mixed=False):
        self.old_root = old_root
        self.new_root = new_root
        self.level = level
        self.compare = compare
        self.include_cosmetic = include_cosmetic
        self.no_layout = no_layout
        self.config = config or {}
        self.allow_mixed = allow_mixed
        patterns = jr.ignore_patterns(old_root, self.config)
        self.old = Tree(old_root, patterns)
        self.new = Tree(new_root, patterns)
        self.hints = []
        self.force_replace = set()
        self.text_only = set()
        self.skipped = set()
        self.cross_units = True

    def profile(self, rel):
        project = pf.lookup(self.config.get("compare"), rel)
        return pf.resolve(rel, self.compare, None, project)

    def _decode(self, data, rel, prof):
        return fileio.decode(data, prof["encoding"], rel, self.allow_mixed)

    def build(self, meta):
        failing = set()
        for _ in range(MAX_ROUNDS):
            try:
                draft = self._draft(meta)
            except _Retry:
                continue
            sealed = copy.deepcopy(draft)
            ctx = engine.Ctx(self.old_root, seal=True, allow_mixed=self.allow_mixed,
                             config=self.config, no_layout=self.no_layout,
                             source_dir=self.new_root)
            try:
                engine.process(sealed, ctx)
            except (Conflict, PatchInvalid) as exc:
                if self.cross_units:
                    self.cross_units = False
                elif self.level == "unit":
                    self.level = "text"
                else:
                    raise
                self.hints.append("create: %s - vereinfachter Versuch" % exc)
                continue
            failing = self._verify(ctx)
            if not failing:
                return sealed, ctx
            self.force_replace.update(failing)
            self.cross_units = False
            self.hints.append("nicht exakt als Einzelaenderungen darstellbar, ganze Datei: "
                              + ", ".join(sorted(failing)))
        raise PatchInvalid("create: Patch fuehrt nicht exakt zum neuen Stand (%s)"
                           % ", ".join(sorted(failing)))

    def _verify(self, ctx):
        failing = set()
        for rel in self.new.files:
            if rel in self.skipped:
                continue
            doc = ctx.docs.get(rel, "untouched")
            if doc == "untouched":
                data = self.old.data(rel) if rel in self.old.files else None
            else:
                data = fileio.encode(doc) if doc is not None else None
            if data != self.new.data(rel):
                failing.add(rel)
        for rel in self.old.files:
            if rel not in self.new.files and ctx.docs.get(rel, "x") is not None:
                failing.add(rel)
        return failing

    def _draft(self, meta):
        entries, deletes, modifies = [], [], []
        old_set, new_set = set(self.old.files), set(self.new.files)
        removed = [r for r in self.old.files if r not in new_set]
        common = [r for r in self.old.files if r in new_set]
        by_content = {}
        for rel in removed:
            by_content.setdefault(self.old.data(rel), []).append(rel)
        unchanged = {}
        for rel in common:
            if self.old.data(rel) == self.new.data(rel):
                unchanged.setdefault(self.old.data(rel), rel)
        for rel in self.new.files:
            if rel in old_set:
                continue
            data = self.new.data(rel)
            if by_content.get(data):
                src = by_content[data].pop(0)
                removed.remove(src)
                entries.append({"path": src, "action": "move", "to": rel})
            elif data in unchanged:
                entries.append({"path": unchanged[data], "action": "copy", "to": rel})
            else:
                entries.append({"path": rel, "action": "add", "source": rel})
        deletes = [{"path": rel, "action": "delete"} for rel in removed]
        plans = {}
        for rel in common:
            if self.old.data(rel) == self.new.data(rel):
                continue
            plan = self._plan(rel)
            if plan is not None:
                plans[rel] = plan
        if self.cross_units:
            self._cross_file(plans)
        modifies = self._order(plans)
        draft = dict(meta)
        draft.update({"format": FORMAT, "generator": "pypatcher " + __version__,
                      "files": entries + modifies + deletes})
        if not draft["files"]:
            raise PatchInvalid("create: keine Unterschiede gefunden")
        return draft

    # -- je Datei --------------------------------------------------------

    def _plan(self, rel):
        odata, ndata = self.old.data(rel), self.new.data(rel)
        replace = {"path": rel, "action": "replace", "source": rel}
        if self.level == "file" or rel in self.force_replace or \
                _is_binary(odata) or _is_binary(ndata):
            return {"entry": replace}
        prof = self.profile(rel)
        if prof["mode"] == "raw":
            return {"entry": replace}
        try:
            odoc = self._decode(odata, rel, prof)
            ndoc = self._decode(ndata, rel, prof)
        except Conflict as exc:
            self.hints.append("%s - ganze Datei" % exc)
            return {"entry": replace}
        if (odoc.eol, odoc.bom, odoc.final_nl) != (ndoc.eol, ndoc.bom, ndoc.final_nl):
            return {"entry": replace}
        compare = None
        if prof["mode"] == "python":
            try:
                oinfo, ninfo = PY.analyze(odoc), PY.analyze(ndoc)
            except ParseError:
                prof = pf.resolve(rel, {"mode": "text"})
                compare = {"mode": "text"}
        if adapters.file_hash(odoc, prof) == adapters.file_hash(ndoc, prof) and \
                not self.include_cosmetic:
            if rel not in self.skipped:
                self.skipped.add(rel)
                self.hints.append("%s: nur kosmetisch veraendert - nicht uebernommen "
                                  "(--include-cosmetic)" % rel)
            return None
        entry = {"path": rel, "action": "modify", "changes": []}
        if compare:
            entry["compare"] = compare
        plan = {"entry": entry, "prof": prof, "odoc": odoc, "ndoc": ndoc,
                "replaces": [], "deletes": [], "adds": []}
        if prof["mode"] == "python" and self.level == "unit" and rel not in self.text_only:
            unit_o, unit_n = PY.detect_unit(odoc, oinfo), PY.detect_unit(ndoc, ninfo)
            diff = UnitDiff(oinfo, ninfo, unit_o, unit_n)
            if diff.run():
                plan.update(oinfo=oinfo, ninfo=ninfo, unit_o=unit_o, unit_n=unit_n,
                            replaces=diff.replaces, deletes=diff.deletes, adds=diff.adds)
        return plan

    def _cross_file(self, plans):
        """Geloeschte Einheit in A == neue Einheit in B -> move_unit; neue Einheit
        == unveraenderte Einheit -> copy_unit."""
        deleted = {}
        for rel, plan in plans.items():
            for unit in plan.get("deletes", []):
                text = tuple(_rel(_lines(plan["oinfo"], unit), unit.indent, plan["unit_o"]) or ())
                if text:
                    deleted.setdefault(text, []).append((rel, unit))
        before = {}   # Datei -> Dateien, die vorher kommen muessen
        moves = 0
        for rel, plan in plans.items():
            for i, (unit, position) in enumerate(plan.get("adds", [])):
                text = tuple(_rel(_lines(plan["ninfo"], unit), unit.indent, plan["unit_n"]) or ())
                for src_rel, src_unit in deleted.get(text, []):
                    if src_rel == rel or self._must_precede(before, src_rel, rel):
                        continue
                    moves += 1
                    mid = "m%d" % moves
                    before.setdefault(src_rel, set()).add(rel)
                    plan["adds"][i] = (unit, position, {"op": "move_unit",
                                                         "from": "%s::%s" % (src_rel, src_unit.path),
                                                         "move_id": mid})
                    src_plan = plans[src_rel]
                    src_plan["deletes"] = [u for u in src_plan["deletes"] if u is not src_unit]
                    src_plan.setdefault("moved_out", []).append(
                        {"op": "move_unit", "unit": src_unit.path, "to": rel, "move_id": mid})
                    deleted[text].remove((src_rel, src_unit))
                    break
        self._before = before
        sources = self._unchanged_units(plans)
        for plan in plans.values():
            for i, add in enumerate(plan.get("adds", [])):
                if len(add) > 2:
                    continue
                unit, position = add
                text = tuple(_rel(_lines(plan["ninfo"], unit), unit.indent, plan["unit_n"]) or ())
                if text in sources:
                    plan["adds"][i] = (unit, position,
                                       {"op": "copy_unit", "from": "%s::%s" % sources[text]})

    def _unchanged_units(self, plans):
        """Einheiten, die in ALT und NEU unveraendert vorhanden sind (Kopier-Quellen)."""
        out = {}
        for rel in self.old.files:
            if rel not in self.new.files or self.profile(rel)["mode"] != "python":
                continue
            plan = plans.get(rel)
            if plan is not None and plan.get("oinfo") is not None:
                oinfo, ninfo, unit = plan["oinfo"], plan["ninfo"], plan["unit_o"]
            elif self.old.data(rel) == self.new.data(rel):
                try:
                    doc = self._decode(self.old.data(rel), rel, self.profile(rel))
                    oinfo = ninfo = PY.analyze(doc)
                    unit = PY.detect_unit(doc, oinfo)
                except (Conflict, ParseError):
                    continue
            else:
                continue
            now = dict((u.path, u) for u in ninfo.units)
            for u in oinfo.units:
                nu = now.get(u.path)
                if u.kind == "header" or nu is None or _lines(oinfo, u) != _lines(ninfo, nu):
                    continue
                text = _rel(_lines(oinfo, u), u.indent, unit)
                if text:
                    out.setdefault(tuple(text), (rel, u.path))
        return out

    @staticmethod
    def _must_precede(before, first, second):
        """Muss first (transitiv) vor second stehen? before[x] = Dateien vor x."""
        seen, todo = set(), list(before.get(second, ()))
        while todo:
            cur = todo.pop()
            if cur == first:
                return True
            if cur not in seen:
                seen.add(cur)
                todo.extend(before.get(cur, ()))
        return False

    def _order(self, plans):
        before = getattr(self, "_before", {}) if self.cross_units else {}
        order, done = [], set()

        def visit(rel, stack=()):
            if rel in done or rel in stack:
                return
            for first in sorted(before.get(rel, ())):   # "from"-Teil zuerst
                visit(first, stack + (rel,))
            done.add(rel)
            order.append(rel)

        for rel in sorted(plans):
            visit(rel)
        return [self._entry(rel, plans[rel]) for rel in order]

    def _entry(self, rel, plan):
        entry = plan["entry"]
        if entry["action"] != "modify":
            return entry
        changes = []
        unit_o = plan.get("unit_o")
        info_n, unit_n = plan.get("ninfo"), plan.get("unit_n")
        for path, unit in plan["replaces"]:
            base = unit.indent
            rel_new = _rel(_lines(info_n, unit), base, unit_n)
            ch = {"op": "replace_unit", "unit": path}
            if rel_new is None:
                ch["literal"] = True
                ch["new"] = list(_lines(info_n, unit))
            else:
                ch["new"] = rel_new
            changes.append(ch)
        for unit in plan["deletes"]:
            changes.append({"op": "delete_unit", "unit": unit.path})
        for add in plan["adds"]:
            unit, position = add[0], add[1]
            if len(add) > 2:
                ch = dict(add[2], position=position)
            else:
                rel_new = _rel(_lines(info_n, unit), unit.indent, unit_n)
                ch = {"op": "add_unit", "position": position}
                if rel_new is None:
                    ch["literal"] = True
                    ch["new"] = list(_lines(info_n, unit))
                else:
                    ch["new"] = rel_new
            changes.append(ch)
        changes.extend(plan.get("moved_out", []))
        current = self._simulate(rel, plan, changes)
        tail = text_changes(current, plan["ndoc"].lines, plan["prof"],
                            unit_o if unit_o is not None else lay.detect_generic(current))
        if tail is None:
            return {"path": rel, "action": "replace", "source": rel}
        changes.extend(tail)
        if not changes:
            return {"path": rel, "action": "replace", "source": rel}
        entry["changes"] = changes
        return entry

    def _simulate(self, rel, plan, changes):
        """Zeilen nach den Einheiten-Aenderungen (ohne Dateiuebergreifendes)."""
        local = [ch for ch in changes if ch["op"] != "move_unit"]
        cross = [ch for ch in changes if ch["op"] == "move_unit"]
        if not local and not cross:
            return list(plan["odoc"].lines)
        draft = {"id": "sim", "title": "sim", "files": []}
        # Dateiuebergreifende Teile im Speicher nachbilden: Quelle als add_unit
        # bzw. Ziel als delete_unit, damit die Zeilen dieser Datei stimmen.
        sim_changes = []
        for ch in changes:
            if ch["op"] == "move_unit" and "from" in ch:
                src_rel, src_path = ch["from"].split("::", 1)
                src_plan_unit = self._source_unit(src_rel, src_path)
                sim_changes.append({"op": "add_unit", "position": ch["position"],
                                    "new": src_plan_unit, "literal": False})
            elif ch["op"] == "move_unit":
                sim_changes.append({"op": "delete_unit", "unit": ch["unit"]})
            else:
                sim_changes.append(copy.deepcopy(ch))
        entry = {"path": rel, "action": "modify", "changes": sim_changes}
        if "compare" in plan["entry"]:
            entry["compare"] = plan["entry"]["compare"]
        draft["files"].append(entry)
        ctx = engine.Ctx(self.old_root, seal=True, allow_mixed=self.allow_mixed,
                         config=self.config, no_layout=True)
        try:
            engine.process(draft, ctx)
        except (Conflict, PatchInvalid) as exc:
            self.hints.append("%s: Einheiten-Vergleich nicht anwendbar (%s) - nur Text" % (rel, exc))
            self.text_only.add(rel)
            if cross:
                self.cross_units = False
            raise _Retry()
        return list(ctx.docs[rel].lines)

    def _source_unit(self, rel, path):
        data = self.old.data(rel)
        prof = self.profile(rel)
        doc = self._decode(data, rel, prof)
        info = PY.analyze(doc)
        unit = PY.find(info, path)
        return _rel(_lines(info, unit), unit.indent, PY.detect_unit(doc, info))


def create(old_root, new_root, meta, **options):
    creator = Creator(old_root, new_root, **options)
    sealed, ctx = creator.build(meta)
    return sealed, ctx, creator.hints + ctx.hints
