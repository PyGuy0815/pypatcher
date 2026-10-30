"""Kommandozeile (manifest.txt Abschnitt 13)."""

import argparse
import os
import sys

from . import __version__, adapters, diff, engine, fileio, journal as jr, patchfile
from . import profile as pf
from .adapters import ParseError
from .errors import Conflict, PatchInvalid, PatcherError, UsageError
from .hashing import state_hash

QUIET = False


def out(msg=""):
    if not QUIET:
        print(msg)


def show_hints(hints):
    for hint in hints:
        out("Hinweis: " + hint)


def confirm(args, question):
    if args.yes or not sys.stdin.isatty():
        return True
    answer = input(question + " [j/N] ").strip().lower()
    return answer in ("j", "ja", "y", "yes")


# -- Patch anwenden / pruefen ----------------------------------------------

def _prepare(args):
    patch = patchfile.load(args.patch)
    patchfile.validate(patch)
    journal = jr.Journal(args.project)
    journal.require()
    journal.require_no_txn()
    applied = journal.applied_ids()
    if patch["id"] in applied:
        raise Conflict("Patch %s ist bereits angewendet" % patch["id"])
    missing = [r for r in patch.get("requires", []) if r not in applied]
    if missing:
        raise Conflict("Patch %s benoetigt zuerst: %s" % (patch["id"], ", ".join(missing)))
    match = args.match or patch.get("match", "file")
    ctx = engine.Ctx(journal.root, match=match, exact=args.exact,
                     allow_mixed=args.allow_mixed_eol, config=journal.config())
    patterns = journal.patterns()
    if match == "state":
        if not patch.get("state_before"):
            raise PatchInvalid("match=state, aber state_before fehlt im Patch")
        current = state_hash(jr.project_hashes(journal.root, patterns))
        if not patchfile.hash_match(patch["state_before"], current):
            raise Conflict("Projektzustand stimmt nicht (state_before)\n    erwartet: %s\n"
                           "    gefunden: %s" % (patch["state_before"], current))
    engine.process(patch, ctx)
    if match == "state" and patch.get("state_after"):
        after = state_hash(jr.project_hashes(journal.root, patterns, ctx.docs, ctx.profiles))
        if not patchfile.hash_match(patch["state_after"], after):
            raise Conflict("Projektzustand nach dem Patch stimmt nicht (state_after)")
    return patch, ctx, journal


def _summary(ctx):
    lines = []
    for rel in sorted(ctx.touched()):
        before, after = ctx.originals[rel], ctx.docs[rel]
        mark = "A" if before is None else ("D" if after is None else "M")
        lines.append("  %s %s" % (mark, rel))
    return lines


def cmd_check(args):
    patch, ctx, _ = _prepare(args)
    out("Patch %s (%s): Pruefung erfolgreich - wuerde aendern:" % (patch["id"], patch["title"]))
    for line in _summary(ctx):
        out(line)
    show_hints(ctx.hints)
    return 0


def cmd_apply(args):
    patch, ctx, journal = _prepare(args)
    out("Patch %s (%s): Pruefung erfolgreich." % (patch["id"], patch["title"]))
    for line in _summary(ctx):
        out(line)
    show_hints(ctx.hints)
    if not confirm(args, "Patch anwenden?"):
        out("Abgebrochen - nichts geaendert.")
        return 1
    journal.commit_apply(patch, ctx)
    out("Patch %s angewendet." % patch["id"])
    return 0


def cmd_rollback(args):
    journal = jr.Journal(args.project)
    journal.require()
    journal.require_no_txn()
    applied = journal.history()["applied"]
    if not applied:
        raise Conflict("keine angewendeten Patches")
    if args.to:
        ids = [e["id"] for e in applied]
        if args.to not in ids:
            raise UsageError("Patch %s ist nicht angewendet" % args.to)
        targets = list(reversed(applied[ids.index(args.to) + 1:]))
    else:
        targets = [applied[-1]]
    if not targets:
        out("Nichts zurueckzunehmen.")
        return 0
    for entry in targets:
        journal.check_after(entry)
    out("Zuruecknehmen (neuester zuerst): " + ", ".join(e["id"] for e in targets))
    if not confirm(args, "Rollback ausfuehren?"):
        out("Abgebrochen - nichts geaendert.")
        return 1
    for entry in targets:
        journal.rollback_one(entry)
        out("Patch %s zurueckgenommen." % entry["id"])
    return 0


def cmd_recover(args):
    journal = jr.Journal(args.project)
    journal.require()
    msg = journal.recover()
    out(msg or "Keine unterbrochene Transaktion - nichts zu tun.")
    return 0


# -- Verwaltung -------------------------------------------------------------

def cmd_init(args):
    journal = jr.Journal(args.project)
    files, hints = journal.init()
    out("Journal angelegt in %s (%d Dateien verwaltet)." % (journal.dir, len(files)))
    show_hints(hints)
    return 0


def cmd_status(args):
    journal = jr.Journal(args.project)
    journal.require()
    state = journal.state()
    applied = journal.history()["applied"]
    out("Projekt      : %s" % journal.root)
    out("Dateien      : %d verwaltet" % len(state["files"]))
    out("Zustand      : %s" % state.get("state_hash"))
    out("Patches      : %d angewendet" % len(applied))
    if applied:
        last = applied[-1]
        out("Letzter      : %s - %s (%s)" % (last["id"], last["title"], last["applied_at"]))
    if journal.txn():
        out("ACHTUNG      : unterbrochene Transaktion - 'patcher.py recover' ausfuehren")
    return 0


def cmd_verify(args):
    journal = jr.Journal(args.project)
    journal.require()
    errors, notes = journal.verify()
    for note in notes:
        out("Hinweis: " + note)
    for err in errors:
        print("FEHLER: " + err)
    if errors:
        return Conflict.exit_code
    out("Ist-Stand entspricht dem Journal.")
    return 0


def cmd_log(args):
    journal = jr.Journal(args.project)
    journal.require()
    history = journal.history()
    out("Angewendet (Stapel, aelteste zuerst):")
    for e in history["applied"]:
        out("  %-24s %s  %s" % (e["id"], e["applied_at"], e["title"]))
    if not history["applied"]:
        out("  -")
    out("Protokoll:")
    for e in history["log"]:
        out("  %s  %-8s %s" % (e["time"], e["action"], e.get("id", "")))
    return 0


# -- Patch erstellen / anzeigen --------------------------------------------

def _save(patch, args):
    if not patchfile.MIN_HASH_LEN <= args.hash_len <= 64:
        raise UsageError("--hash-len muss zwischen %d und 64 liegen" % patchfile.MIN_HASH_LEN)
    paths, total = patchfile.save(patch, args.output, level=args.verify,
                                  hash_len=args.hash_len, max_size=args.max_size)
    size = sum(os.path.getsize(p) for p in paths)
    out("Patch %s (%d Zeichen, Pruefstufe %s, Hash %s) -> %s"
        % (patch["id"], size, args.verify, total, ", ".join(paths)))


def cmd_seal(args):
    patch = patchfile.load(args.draft, verify=False)
    patchfile.validate(patch, sealed=False)
    base = os.path.abspath(args.base)
    if not os.path.isdir(base):
        raise UsageError("%s ist kein Verzeichnis" % args.base)
    journal = jr.Journal(base)
    config = journal.config() if journal.exists else {}
    patch["generator"] = "pypatcher " + __version__
    patch.setdefault("created", jr.now())
    ctx = engine.Ctx(base, seal=True, allow_mixed=args.allow_mixed_eol, config=config,
                     no_layout=args.no_layout,
                     source_dir=os.path.dirname(os.path.abspath(args.draft)))
    engine.process(patch, ctx)
    if args.state or patch["match"] == "state":
        patterns = jr.ignore_patterns(base, config)
        patch["state_before"] = state_hash(jr.project_hashes(base, patterns))
        patch["state_after"] = state_hash(jr.project_hashes(base, patterns, ctx.docs, ctx.profiles))
    patchfile.validate(patch)
    _save(patch, args)
    for line in _summary(ctx):
        out(line)
    show_hints(ctx.hints)
    return 0


def cmd_create(args):
    old, new = os.path.abspath(args.old), os.path.abspath(args.new)
    for path in (old, new):
        if not os.path.isdir(path):
            raise UsageError("%s ist kein Verzeichnis" % path)
    journal = jr.Journal(old)
    meta = {"id": args.id, "title": args.title, "created": jr.now(),
            "requires": args.requires or [], "match": "file"}
    if args.description:
        meta["description"] = args.description
    if args.author:
        meta["author"] = args.author
    patchfile.validate(dict(meta, files=[{"path": "x", "action": "delete"}]), sealed=False)
    compare = pf.parse_short(args.compare) if args.compare else None
    patch, ctx, hints = diff.create(old, new, meta, level=args.level, compare=compare,
                                    include_cosmetic=args.include_cosmetic,
                                    no_layout=args.no_layout,
                                    config=journal.config() if journal.exists else {},
                                    allow_mixed=args.allow_mixed_eol)
    patchfile.validate(patch)
    _save(patch, args)
    for entry in patch["files"]:
        target = " -> " + entry["to"] if entry.get("to") else ""
        count = " (%d Aenderungen)" % len(entry["changes"]) if entry.get("changes") else ""
        out("  %-7s %s%s%s" % (entry["action"], entry["path"], target, count))
    show_hints(hints)
    return 0


def _indent_unit(args, entry):
    """Darstellung einer Einrueckungs-Ebene: --indent oder wie in der Datei (layout)."""
    choice = args.indent or "file"
    if choice == "tab":
        return "\t"
    if choice != "file":
        return " " * int(choice)
    layout = entry.get("layout") or {}
    if layout.get("indent") == "tabs":
        return "\t"
    return " " * layout.get("width", 4)


def _block(lines, mark, unit, literal):
    shown = []
    for line in lines or []:
        if not literal:
            n = len(line) - len(line.lstrip("\t"))
            line = unit * n + line[n:]
        shown.append("      %s %s" % (mark, line))
    return shown


def cmd_show(args):
    try:
        patch = patchfile.load(args.patch)
        ok = True
    except PatchInvalid as exc:
        patch = patchfile.load(args.patch, verify=False)
        ok = False
        print("FEHLER: %s" % exc)
    out("Patch     : %s - %s" % (patch.get("id"), patch.get("title")))
    for key, label in (("generator", "Erzeugt"), ("author", "Autor"), ("created", "Datum"),
                       ("match", "Pruefung")):
        if patch.get(key):
            out("%-10s: %s" % (label, patch[key]))
    if patch.get("requires"):
        out("Benoetigt : %s" % ", ".join(patch["requires"]))
    for line in (patch.get("description") or "").splitlines():
        out("Beschreib.: %s" % line)
    out("Patch-Hash: %s" % ("OK" if ok else "FEHLER"))
    for n, entry in enumerate(patch.get("files", []), 1):
        prof = patchfile.profile_short(entry.get("compare")) or "auto"
        target = " -> " + entry["to"] if entry.get("to") else ""
        checks = "Hash" if entry.get("hash_before") or entry.get("hash_after") else "ohne Hash"
        out("")
        out("[%d] %s %s%s  (%s, %s)" % (n, patchfile.CMD_OF[entry["action"]], entry["path"],
                                       target, prof, checks))
        unit = _indent_unit(args, entry)
        if entry["action"] in ("add", "replace") and args.diff:
            if isinstance(entry.get("new"), dict):
                out("      (Binaerinhalt, %d Zeichen Base64)" % len(entry["new"]["base64"]))
            else:
                for line in _block(entry.get("new"), "+", unit, entry.get("literal")):
                    out(line)
        for i, ch in enumerate(entry.get("changes", []), 1):
            details = [ch.get(k) for k in ("unit", "from", "position") if ch.get(k)]
            for key in ("as", "to", "scope", "move_id"):
                if ch.get(key):
                    details.append("%s=%s" % (key, ch[key]))
            checks = "Funktion geprueft" if ch.get("unit_hash_before") or \
                ch.get("unit_hash_after") else ""
            out("    %d. %s %s%s" % (i, patchfile.CMD_OF[ch["op"]], " ".join(details),
                                     "  (%s)" % checks if checks else ""))
            if not args.diff:
                continue
            lit = ch.get("literal")
            for key, mark in (("anchor", "@"), ("old", "-"), ("new", "+")):
                if ch["op"] in ("copy_unit", "move_unit"):
                    break
                for line in _block(ch.get(key), mark, unit, lit):
                    out(line)
    return 0 if ok else PatchInvalid.exit_code


def cmd_units(args):
    rel = args.file.replace(os.sep, "/")
    prof = pf.resolve(rel, pf.parse_short(args.compare) if args.compare else None)
    doc = fileio.load(args.file, rel, prof, args.allow_mixed_eol)
    out("%s  (%s)" % (rel, pf.profile_id(prof)))
    out("Datei-Hash : %s" % adapters.file_hash(doc, prof))
    out("Text-Hash  : %s" % adapters.text_hash(doc))
    ua = adapters.get(prof["mode"])
    if not ua.has_units:
        out("(keine Einheiten bei mode %s)" % prof["mode"])
        return 0
    try:
        info = ua.analyze(doc)
        unit = ua.detect_unit(doc, info)
    except ParseError as exc:
        raise Conflict("%s: kein gueltiges %s (%s)" % (rel, ua.label, exc))
    out("Einrueckung: %s" % ("unbekannt" if unit is None else
                             "Tab" if unit == "\t" else "%d Leerzeichen" % len(unit)))
    out("")
    out("%-40s %-9s %-11s %s" % ("Einheit", "Art", "Zeilen", "Hash"))
    for u in info.units:
        span = "%d-%d" % (u.start + 1, u.end) if u.end > u.start else "-"
        out("%-40s %-9s %-11s %s" % (u.path, u.kind, span, adapters.unit_hash(doc, info, u, prof)))
    return 0


# -- Hauptprogramm ----------------------------------------------------------

def build_parser():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("-v", "--verbose", action="store_true", help="ausfuehrliche Ausgabe")
    common.add_argument("-q", "--quiet", action="store_true", help="nur Fehler ausgeben")
    common.add_argument("-y", "--yes", action="store_true", help="keine Rueckfragen")
    common.add_argument("--allow-mixed-eol", action="store_true",
                        help="Dateien mit gemischten Zeilenenden zulassen")
    project = argparse.ArgumentParser(add_help=False)
    project.add_argument("-p", "--project", default=".", help="Projektwurzel (Standard: .)")

    parser = argparse.ArgumentParser(
        prog="patcher.py", description="Hash-gepruefter Patcher (pypatcher %s)" % __version__)
    parser.add_argument("--version", action="version", version="pypatcher " + __version__)
    sub = parser.add_subparsers(dest="command", metavar="BEFEHL")
    sub.required = True

    def add(name, func, help_text, parents=(common,)):
        p = sub.add_parser(name, parents=list(parents), help=help_text, description=help_text)
        p.set_defaults(func=func)
        return p

    add("init", cmd_init, "Journal anlegen, Ist-Stand als Ausgangszustand", (common, project))
    add("status", cmd_status, "Ueberblick ueber Zustand und Patches", (common, project))
    add("verify", cmd_verify, "Ist-Stand gegen das Journal pruefen", (common, project))
    add("log", cmd_log, "Historie anzeigen", (common, project))
    p = add("units", cmd_units, "Einheiten einer Datei mit Hashes")
    p.add_argument("file")
    p.add_argument("--compare", help='Profil, z.B. "python" oder "text,whitespace=keep"')
    p = add("show", cmd_show, "Patch lesbar anzeigen und Hashes pruefen")
    p.add_argument("patch", help="Patchdatei (bei Paketen: eine beliebige davon)")
    p.add_argument("--no-diff", dest="diff", action="store_false", help="ohne Inhalte")
    p.add_argument("--indent", help="Darstellung einer Einrueckungs-Ebene: Anzahl Leerzeichen, "
                   "'tab' oder 'file' (wie in der Datei, Standard)")
    def output_options(p):
        p.add_argument("--verify", choices=patchfile.LEVELS, default="std",
                       help="Pruefstufe: min = nur Datei-Hashes, std = zusaetzlich Funktionen "
                       "(Standard), full = alles inkl. Zwischenstaende und exaktem Text")
        p.add_argument("--hash-len", type=int, default=patchfile.DEFAULT_HASH_LEN,
                       help="Laenge der Hashes in Hex-Zeichen (8-64, Standard 16)")
        p.add_argument("--max-size", type=int,
                       help="in Pakete mit hoechstens N Zeichen teilen")

    p = add("seal", cmd_seal, "Entwurf versiegeln (Hashes und Altinhalte eintragen)")
    p.add_argument("draft", metavar="ENTWURF")
    p.add_argument("--base", required=True, help="Referenzprojekt (Stand vor dem Patch)")
    p.add_argument("-o", "--output", required=True, help="Ausgabe *.ppatch")
    p.add_argument("--no-layout", action="store_true", help="kein layout als Check eintragen")
    p.add_argument("--state", action="store_true", help="state_before/after eintragen")
    output_options(p)
    p = add("create", cmd_create, "Patch aus zwei Staenden erzeugen (ALT -> NEU)")
    p.add_argument("old", metavar="ALT", help="Ausgangsstand (wie im Zielprojekt)")
    p.add_argument("new", metavar="NEU", help="neuer Stand")
    p.add_argument("-o", "--output", required=True, help="Ausgabe *.ppatch")
    p.add_argument("--id", required=True, help="Patch-ID")
    p.add_argument("--title", required=True, help="Kurzbeschreibung")
    p.add_argument("--description", help="ausfuehrliche Beschreibung")
    p.add_argument("--author", help="Autor")
    p.add_argument("--requires", action="append", metavar="ID",
                   help="vorher noetiger Patch (mehrfach moeglich)")
    p.add_argument("--compare", help='Profil fuer alle Dateien, z.B. "python"')
    p.add_argument("--level", choices=("unit", "text", "file"), default="unit",
                   help="feinste Ebene der Aenderungen (Standard: unit)")
    p.add_argument("--include-cosmetic", action="store_true",
                   help="auch rein kosmetisch veraenderte Dateien uebernehmen")
    p.add_argument("--no-layout", action="store_true", help="kein layout als Check eintragen")
    output_options(p)
    for name, func, text in (("check", cmd_check, "Probelauf, aendert nichts"),
                             ("apply", cmd_apply, "Patch anwenden")):
        p = add(name, func, text, (common, project))
        p.add_argument("patch", help="Patchdatei (bei Paketen: eine beliebige davon)")
        p.add_argument("--match", choices=("file", "state", "unit"),
                       help="Pruefumfang (Standard: aus dem Patch)")
        p.add_argument("--exact", action="store_true",
                       help="zusaetzlich Text-Hashes pruefen (Kommentare/Leerraum exakt)")
    p = add("rollback", cmd_rollback, "Patches zuruecknehmen", (common, project))
    p.add_argument("--to", metavar="ID", help="alle Patches nach ID zuruecknehmen")
    add("recover", cmd_recover, "Unterbrochene Transaktion bereinigen", (common, project))
    return parser


def main(argv=None):
    global QUIET
    args = build_parser().parse_args(argv)
    QUIET = args.quiet
    try:
        return args.func(args)
    except PatcherError as exc:
        print("FEHLER: %s" % exc, file=sys.stderr)
        return exc.exit_code
    except KeyboardInterrupt:
        print("Abgebrochen.", file=sys.stderr)
        return 1
    except Exception as exc:  # interner Fehler
        if args.verbose:
            raise
        print("INTERNER FEHLER: %s: %s (Details mit -v)" % (type(exc).__name__, exc),
              file=sys.stderr)
        return 5
