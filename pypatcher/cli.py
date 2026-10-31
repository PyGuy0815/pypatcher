"""Kommandozeile (manifest.txt Abschnitt 13)."""

import argparse
import copy
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipapp

from . import __version__, adapters, console, diff, engine, fileio, flow, journal as jr, patchfile
from . import profile as pf
from .adapters import ParseError
from .errors import Conflict, PatchInvalid, PatcherError, TestsFailed, UsageError
from .hashing import state_hash

QUIET = False
REPORT = None           # --json: Ergebnis als ein JSON-Objekt statt Textausgabe
JSON_FORMAT = "pypatcher-result/1"
RESULT_CODES = {0: "OK", 1: "USAGE", 2: "PATCH_INVALID", 3: "CONFLICT", 4: "JOURNAL",
                5: "INTERNAL", 6: "TESTS_FAILED"}


def report(**fields):
    if REPORT is not None:
        REPORT.update(fields)


def out(msg="", kind=None):
    """Normale Ausgabe (entfaellt mit -q und --json); kind faerbt die ganze Zeile
    (ok/warn/info), sonst nur ein Kennwort am Anfang (Hinweis, FEHLER ...)."""
    if not QUIET and REPORT is None:
        print(console.paint(msg, kind) if kind else console.label(msg))


def say(msg, stream=None):
    """Ausgabe auch mit -q (Fehler, Ergebniszeile); mit --json gesammelt."""
    if REPORT is not None:
        REPORT.setdefault("messages", []).append(msg)
        return
    stream = stream or sys.stdout
    print(console.label(msg, stream), file=stream)


def show_hints(hints):
    if REPORT is not None:
        REPORT.setdefault("hints", []).extend(hints)
        return
    for hint in hints:
        out("Hinweis: " + hint)


def confirm(args, question):
    if REPORT is not None and not args.yes:
        raise UsageError("--json verlangt -y (oder -n fuer einen Probelauf) - keine Rueckfragen")
    if args.yes or not sys.stdin.isatty():
        return True
    answer = input(question + " [j/N] ").strip().lower()
    return answer in ("j", "ja", "y", "yes")


# -- Ablauf (PRINT/INPUT/IF ...) ---------------------------------------------

def _answers_file(path):
    """--answers: NAME=WERT je Zeile (# = Kommentar) oder ein JSON-Objekt."""
    try:
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
    except (OSError, UnicodeDecodeError) as exc:
        raise UsageError("--answers %s: nicht lesbar (%s)" % (path, exc))
    if text.lstrip().startswith("{"):
        try:
            data = json.loads(text)
        except ValueError as exc:
            raise UsageError("--answers %s: kein gueltiges JSON (%s)" % (path, exc))
        if not isinstance(data, dict):
            raise UsageError("--answers %s: JSON-Objekt erwartet" % path)
        return dict((str(k), v if isinstance(v, str) else json.dumps(v)) for k, v in data.items())
    values = {}
    for n, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        var, sep, value = line.partition("=")
        if not sep or not var.strip():
            raise UsageError("--answers %s, Zeile %d: NAME=WERT erwartet" % (path, n))
        values[var.strip()] = value.strip()
    return values


def _preset(args):
    values = {}
    if getattr(args, "answers", None):
        values.update(_answers_file(args.answers))
    for item in getattr(args, "set", None) or []:
        var, sep, value = item.partition("=")
        if not sep or not var:
            raise UsageError("--set erwartet NAME=WERT, nicht %r" % item)
        values[var] = value
    return values


def _ask(args):
    def ask(item):
        choices, default = item["choices"], item["default"]
        if args.yes or not sys.stdin.isatty() or REPORT is not None:
            if default is not None:
                return default
            raise UsageError("Antwort fuer %s fehlt - mit --set %s=WERT angeben"
                             % (item["var"], item["var"]))
        text = item["prompt"] or item["var"]
        if choices:
            text += " [%s]" % "/".join(choices)
        if default is not None:
            text += " (Vorgabe: %s)" % default
        while True:
            answer = input(text + ": ").strip()
            if not answer and default is not None:
                return default
            if answer and (not choices or answer in choices):
                return answer
            print("Bitte eine der Moeglichkeiten angeben: %s" % ", ".join(choices or ["Text"]))
    return ask


def _plan(patch, args):
    """Ablauf bestimmen: Fragen beantworten, Zweige waehlen (vor jeder Pruefung)."""
    def say_flow(text):
        if REPORT is not None:
            REPORT.setdefault("print", []).append(text)
        else:
            print("| " + text)

    entries, answers = flow.plan(patch, _ask(args), say=say_flow,
                                 preset=_preset(args))
    if patch.get("flow"):
        out("Ablauf: %d Dateibefehle%s" % (len(entries), "" if not answers else
                                           " (" + ", ".join("%s=%s" % kv for kv in
                                                            sorted(answers.items())) + ")"))
    return entries, answers


# -- Patch anwenden / pruefen ----------------------------------------------

def _journal_checks(patch, args):
    journal = jr.Journal(args.project)
    journal.require()
    journal.require_no_txn()
    applied = journal.applied_ids()
    if patch["id"] in applied:
        raise Conflict("Patch %s ist bereits angewendet" % patch["id"])
    missing = [r for r in patch.get("requires", []) if r not in applied]
    if missing:
        raise Conflict("Patch %s benoetigt zuerst: %s" % (patch["id"], ", ".join(missing)))
    return journal


def _prepare(args):
    patch = patchfile.load(args.patch)
    patchfile.validate(patch)
    journal = _journal_checks(patch, args)
    entries, answers = _plan(patch, args)
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
    engine.process(patch, ctx, entries)
    _ignored_hint(ctx, journal)
    if match == "state" and patch.get("state_after") and not patch.get("flow"):
        after = state_hash(jr.project_hashes(journal.root, patterns, ctx.docs, ctx.profiles))
        if not patchfile.hash_match(patch["state_after"], after):
            raise Conflict("Projektzustand nach dem Patch stimmt nicht (state_after)")
    return patch, ctx, journal, answers


def _ignored_hint(ctx, journal):
    """Patch aendert Dateien, die pypatcher ignoriert (z.B. .DS_Store)."""
    patterns = journal.patterns()
    ignored = sorted(rel for rel in ctx.touched() if jr.is_ignored(rel, patterns))
    if ignored:
        ctx.hint("%d Datei(en) werden von pypatcher ignoriert (Vorgaben/.patcherignore) - "
                 "dafuer ist kein Patch noetig: %s%s" % (
                     len(ignored), ", ".join(ignored[:5]), " ..." if len(ignored) > 5 else ""))


def _summary(ctx):
    lines, files = [], []
    for rel in sorted(ctx.touched()):
        before, after = ctx.originals[rel], ctx.docs[rel]
        mark = "A" if before is None else ("D" if after is None else "M")
        lines.append("  %s %s" % (mark, rel))
        files.append({"path": rel, "change": {"A": "add", "D": "delete", "M": "modify"}[mark]})
    report(files=files)
    return lines


def _report_patch(patch, applied, answers=None):
    report(patch={"id": patch["id"], "title": patch.get("title", ""),
                  "sealed": "patch_hash" in patch}, applied=applied,
           answers=answers or {})


def cmd_check(args):
    patch, ctx, _, answers = _prepare(args)
    _report_patch(patch, False, answers)
    out("Patch %s (%s): Pruefung erfolgreich - wuerde aendern:" % (patch["id"], patch["title"]), "ok")
    for line in _summary(ctx):
        out(line)
    show_hints(ctx.hints)
    return 0


def cmd_apply(args):
    patch, ctx, journal, answers = _prepare(args)
    _report_patch(patch, False, answers)
    out("Patch %s (%s): Pruefung erfolgreich." % (patch["id"], patch["title"]), "ok")
    for line in _summary(ctx):
        out(line)
    show_hints(ctx.hints)
    if not confirm(args, "Patch anwenden?"):
        out("Abgebrochen - nichts geaendert.", "warn")
        return 1
    journal.commit_apply(patch, ctx, answers)
    report(applied=True)
    out("Patch %s angewendet." % patch["id"], "ok")
    return _after_apply(args, journal, patch, ctx)


def cmd_run(args):
    """Entwurf (z.B. von einer KI) direkt pruefen und anwenden: Ablauf, Inhalts-
    pruefung gegen das Zielprojekt, Vorschau, Rueckfrage, Sicherung."""
    patch = patchfile.load(args.patch, verify=False)
    sealed = "patch_hash" in patch
    if sealed:
        patch = patchfile.load(args.patch)
    patchfile.validate(patch, sealed=False)
    journal = _journal_checks(patch, args)
    entries, answers = _plan(patch, args)
    ctx = engine.Ctx(journal.root, seal=not sealed, allow_mixed=args.allow_mixed_eol,
                     config=journal.config(),
                     source_dir=os.path.dirname(os.path.abspath(args.patch)))
    engine.process(patch, ctx, entries)
    _ignored_hint(ctx, journal)
    _report_patch(patch, False, answers)
    report(dry_run=bool(args.dry_run))
    out("Patch %s (%s): %s erfolgreich - %s:"
        % (patch["id"], patch["title"], "Hash-Pruefung" if sealed else "Inhaltspruefung",
           "wuerde aendern" if args.dry_run else "aendert"), "ok")
    for line in _summary(ctx):
        out(line)
    show_hints(ctx.hints)
    if args.dry_run:
        out("Probelauf - nichts geaendert.", "info")
        return 0
    if not confirm(args, "Patch anwenden?"):
        out("Abgebrochen - nichts geaendert.", "warn")
        return 1
    journal.commit_apply(patch, ctx, answers)
    report(applied=True)
    out("Patch %s angewendet (zuruecknehmen: patcher.py rollback)." % patch["id"], "ok")
    return _after_apply(args, journal, patch, ctx)


def _test_command(args, journal):
    if args.no_test:
        return None
    command = args.test or journal.config().get("test")
    if command is not None and not isinstance(command, str):
        raise UsageError("config.json: \"test\" muss ein Befehl (Text) sein")
    return command or None


def _run_tests(command, root):
    """Testbefehl im Projekt ausfuehren; mit -q wird die Ausgabe nur bei
    Fehlern gezeigt. -> True bei Erfolg."""
    out("Tests: %s" % command)
    sys.stdout.flush()
    capture = QUIET or REPORT is not None
    try:
        result = subprocess.run(command, shell=True, cwd=root, stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE if capture else None,
                                stderr=subprocess.STDOUT if capture else None)
    except OSError as exc:
        raise UsageError("Testbefehl nicht ausfuehrbar: %s" % exc)
    text = (result.stdout or b"").decode("utf-8", "replace")
    report(tests={"command": command, "ok": result.returncode == 0,
                  "exit_code": result.returncode,
                  "output": text[-4000:] if result.returncode else ""})
    if result.returncode and QUIET and REPORT is None and text:
        sys.stderr.write(text)
    return result.returncode == 0


def _after_apply(args, journal, patch, ctx):
    """Nach dem Anwenden: Journal pruefen (vor den Tests - Tests duerfen
    Dateien anlegen), Tests (--test oder config.json "test"), eine
    Ergebniszeile (auch mit -q)."""
    count = len(ctx.touched())
    parts = ["%d Datei%s" % (count, "" if count == 1 else "en")]
    errors, notes = journal.verify()
    _report_journal(journal, errors, notes)
    for note in notes:
        out("Hinweis: " + note)
    for err in errors:
        say("FEHLER: " + err)
    unmanaged = len(journal.unmanaged)
    if errors:
        parts.append("Journal: %d Abweichung%s" % (len(errors), "" if len(errors) == 1 else "en"))
    else:
        parts.append("Journal OK" + (" (%d unverwaltete Datei%s)"
                                     % (unmanaged, "" if unmanaged == 1 else "en")
                                     if unmanaged else ""))
    command = _test_command(args, journal)
    tests_ok = True
    if command:
        tests_ok = _run_tests(command, journal.root)
        parts.append("Tests OK" if tests_ok else "Tests FEHLGESCHLAGEN")
    rollback = args.rollback_on_failure or journal.config().get("rollback_on_failure") is True
    if not tests_ok:
        if rollback:
            journal.rollback_one(journal.history()["applied"][-1])
            parts.append("zurueckgenommen")
            report(applied=False, rolled_back=True)
        say("FEHLER %s | %s" % (patch["id"], " | ".join(parts)))
        raise TestsFailed("Tests fehlgeschlagen - Patch %s %s" % (
            patch["id"], "zurueckgenommen" if rollback else
            "bleibt angewendet (zuruecknehmen: patcher.py rollback)"))
    say("%s %s | %s" % ("OK" if not errors else "FEHLER", patch["id"], " | ".join(parts)))
    return Conflict.exit_code if errors else 0


def _report_journal(journal, errors, notes):
    report(journal={"ok": not errors, "deviations": errors, "notes": notes,
                    "unmanaged": list(journal.unmanaged)})


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
        out("Abgebrochen - nichts geaendert.", "warn")
        return 1
    for entry in targets:
        journal.rollback_one(entry)
        out("Patch %s zurueckgenommen." % entry["id"], "ok")
    report(rolled_back=[e["id"] for e in targets])
    return 0


def cmd_recover(args):
    journal = jr.Journal(args.project)
    journal.require()
    msg = journal.recover()
    report(recovered=msg)
    out(msg or "Keine unterbrochene Transaktion - nichts zu tun.")
    return 0


# -- Verwaltung -------------------------------------------------------------

def cmd_init(args):
    journal = jr.Journal(args.project)
    renew = args.force and journal.exists
    files, hints = journal.init(args.force)
    report(managed=len(files))
    out("Journal %s in %s (%d Dateien verwaltet)."
        % ("neu angelegt" if renew else "angelegt", journal.dir, len(files)), "ok")
    show_hints(hints)
    return 0


def cmd_status(args):
    journal = jr.Journal(args.project)
    journal.require()
    state = journal.state()
    applied = journal.history()["applied"]
    report(status={"project": journal.root, "managed": len(state["files"]),
                   "state_hash": state.get("state_hash"),
                   "applied": [{"id": e["id"], "title": e["title"], "applied_at": e["applied_at"]}
                               for e in applied],
                   "interrupted": bool(journal.txn())})
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
    _report_journal(journal, errors, notes)
    for note in notes:
        out("Hinweis: " + note)
    for err in errors:
        say("FEHLER: " + err)
    if errors:
        return Conflict.exit_code
    out("Ist-Stand entspricht dem Journal.", "ok")
    return 0


def cmd_log(args):
    journal = jr.Journal(args.project)
    journal.require()
    history = journal.history()
    report(history={"applied": [{"id": e["id"], "title": e["title"],
                                 "applied_at": e["applied_at"]} for e in history["applied"]],
                    "log": history["log"]})
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
                                  hash_len=args.hash_len, max_size=args.max_size,
                                  compress=args.compress)
    _report(patch, paths, "Pruefstufe %s, Hash %s" % (args.verify, total))


def _report(patch, paths, info):
    sizes, packed = [], False
    for path in paths:
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
        sizes.append(len(text))
        packed = packed or "\nUSES " in text and " z64" in text.split("\nUSES ", 1)[1].split("\n")[0]
    if packed:
        info = "Daten komprimiert, " + info
    parts = " in %d Paketen (max. %d)" % (len(paths), max(sizes)) if len(paths) > 1 else ""
    out("Patch %s (%d Zeichen%s, %s) -> %s"
        % (patch["id"], sum(sizes), parts, info, ", ".join(paths)))


def cmd_bundle(args):
    """pypatcher als eine Datei (zipapp), z.B. zum Hochladen in eine KI-Sandbox."""
    package = os.path.dirname(os.path.abspath(__file__))
    if not os.path.isdir(package):
        raise UsageError("pypatcher laeuft bereits als Einzeldatei - diese einfach kopieren")
    with tempfile.TemporaryDirectory() as tmp:
        shutil.copytree(package, os.path.join(tmp, "pypatcher"),
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        with open(os.path.join(tmp, "__main__.py"), "w", encoding="utf-8") as handle:
            handle.write("import sys\nfrom pypatcher.cli import main\nsys.exit(main())\n")
        zipapp.create_archive(tmp, args.output, interpreter="/usr/bin/env python3",
                              compressed=True)
    out("pypatcher %s als Einzeldatei -> %s (%d Bytes); Aufruf: python3 %s BEFEHL ..."
        % (__version__, args.output, os.path.getsize(args.output), args.output))
    return 0


def cmd_pack(args):
    """Patch oder Entwurf umpacken: komprimieren/entpacken, in Pakete teilen.
    Inhalt und Hashes bleiben unveraendert."""
    patch = patchfile.load(args.patch, verify=False)
    sealed = "patch_hash" in patch
    if sealed:
        patch = patchfile.load(args.patch)
    paths, total = patchfile.save(patch, args.output, level="full", hash_len=None,
                                  max_size=args.max_size, compress=args.compress,
                                  draft=not sealed)
    _report(patch, paths, "Hash %s" % total if sealed else "Entwurf ohne Hash")
    return 0


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
    source_dir = os.path.dirname(os.path.abspath(args.draft))
    if patch.get("flow"):
        return _seal_flow(patch, args, base, config, source_dir)
    ctx = engine.Ctx(base, seal=True, allow_mixed=args.allow_mixed_eol, config=config,
                     no_layout=args.no_layout, source_dir=source_dir)
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


HASH_KEYS = ("hash_before", "hash_after", "text_hash_before", "text_hash_after")
CHANGE_KEYS = ("file_hash_before", "file_hash_after", "unit_hash_before", "unit_hash_after",
               "source_unit_hash", "unit_after", "unit")


def _merge(versions, keys, what, hints):
    """Werte, die in allen Ablaeufen gleich sind, bleiben; abweichende entfallen."""
    base = copy.deepcopy(versions[0])
    for key in keys:
        values = set(str(v.get(key)) for v in versions)
        if len(values) > 1:
            base.pop(key, None)
            hints.append("%s: %s haengt vom Ablauf ab - wird nicht geprueft" % (what, key))
    return base


def _seal_flow(patch, args, base, config, source_dir):
    """seal mit Ablaufsteuerung: jeder moegliche Ablauf wird im Speicher
    durchgespielt; Hashes, die in allen Ablaeufen gleich sind, werden eingetragen."""
    if args.state or patch["match"] == "state":
        raise PatchInvalid("STATE/--state ist mit Ablaufsteuerung nicht moeglich")
    results, hints = [], []
    for answers, indices in flow.explore(patch):
        trial = copy.deepcopy(patch)
        ctx = engine.Ctx(base, seal=True, allow_mixed=args.allow_mixed_eol, config=config,
                         no_layout=args.no_layout, source_dir=source_dir)
        label = ", ".join("%s=%s" % kv for kv in sorted(answers.items())) or "ohne Fragen"
        try:
            engine.process(trial, ctx, [trial["files"][i] for i in indices])
        except (Conflict, PatchInvalid) as exc:
            raise type(exc)("Ablauf (%s): %s" % (label, exc))
        results.append((set(indices), trial))
        hints.extend("Ablauf (%s): %s" % (label, h) for h in ctx.hints)
    for i in range(len(patch["files"])):
        versions = [trial["files"][i] for indices, trial in results if i in indices]
        what = "Dateibefehl %d (%s)" % (i + 1, patch["files"][i]["path"])
        if not versions:
            hints.append("%s wird in keinem Ablauf ausgefuehrt" % what)
            continue
        entry = _merge(versions, HASH_KEYS, what, hints)
        for k, ch in enumerate(entry.get("changes") or []):
            merged = _merge([v["changes"][k] for v in versions], CHANGE_KEYS,
                            "%s, Aenderung %d" % (what, k + 1), hints)
            entry["changes"][k] = merged
        patch["files"][i] = entry
    patchfile.validate(patch)
    _save(patch, args)
    out("  %d moegliche Ablaeufe geprueft" % len(results))
    show_hints(hints)
    return 0


def cmd_create(args):
    old, new = os.path.abspath(args.old), os.path.abspath(args.new)
    for path in (old, new):
        if not os.path.isdir(path):
            raise UsageError("%s ist kein Verzeichnis - create vergleicht zwei Verzeichnisse "
                             "(ALT, NEU). Fuer einzelne Dateien beide Staende in eigene "
                             "Verzeichnisse legen (alt/ips.py, neu/ips.py) oder einen "
                             "Entwurf mit REP schreiben" % path)
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
        if "patch_hash" in patch:
            say("FEHLER: %s" % exc)
    out("Patch     : %s - %s" % (patch.get("id"), patch.get("title")))
    for key, label in (("generator", "Erzeugt"), ("author", "Autor"), ("created", "Datum"),
                       ("match", "Pruefung")):
        if patch.get(key):
            out("%-10s: %s" % (label, patch[key]))
    if patch.get("requires"):
        out("Benoetigt : %s" % ", ".join(patch["requires"]))
    for line in (patch.get("description") or "").splitlines():
        out("Beschreib.: %s" % line)
    out("Patch-Hash: %s" % ("OK" if ok else ("Entwurf (ohne Hash)" if "patch_hash" not in patch
                                            else "FEHLER")))
    if patch.get("compressed"):
        out("Daten    : komprimiert (z64) - hier entpackt dargestellt")
    if patch.get("flow"):
        depth = 0
        for item in patch["flow"]:
            if item["stmt"] in ("elif", "else", "end"):
                depth -= 1
            if item["stmt"] == "file":
                _show_entry(args, item["index"] + 1, patch["files"][item["index"]], "  " * depth)
            else:
                out("%s%s" % ("  " * depth, patchfile.flow_line(item)))
            if item["stmt"] in ("if", "elif", "else"):
                depth += 1
    else:
        for n, entry in enumerate(patch.get("files", []), 1):
            _show_entry(args, n, entry, "")
    return 0 if ok or "patch_hash" not in patch else PatchInvalid.exit_code


def _show_entry(args, n, entry, pad):
    target = " -> " + entry["to"] if entry.get("to") else ""
    if entry["action"] in patchfile.DIR_ACTIONS:
        files = entry.get("files")
        out("%s[%d] %s %s%s  (%s)" % (
            pad, n, patchfile.CMD_OF[entry["action"]], entry["path"], target,
            "Verzeichnis, %d Dateien versiegelt" % len(files) if files is not None
            else "Verzeichnis, Dateiliste beim Anwenden"))
        if args.diff:
            for item in files or []:
                out("      %s  %s" % (patchfile.short(item["hash"]), item["path"]))
        return
    prof = patchfile.profile_short(entry.get("compare")) or "auto"
    checks = "Hash" if entry.get("hash_before") or entry.get("hash_after") else "ohne Hash"
    out("%s[%d] %s %s%s  (%s, %s)" % (pad, n, patchfile.CMD_OF[entry["action"]],
                                     entry["path"], target, prof, checks))
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
    common.add_argument("--color", choices=console.MODES, default="auto",
                        help="Farben: auto (nur am Terminal, nicht bei NO_COLOR), always, never")
    project = argparse.ArgumentParser(add_help=False)
    project.add_argument("-p", "--project", default=".", help="Projektwurzel (Standard: .)")
    project.add_argument("--json", action="store_true",
                         help="Ergebnis als ein JSON-Objekt (fuer KIs/Werkzeuge; Format "
                         "%s)" % JSON_FORMAT)

    parser = argparse.ArgumentParser(
        prog="patcher.py", description="Hash-gepruefter Patcher (pypatcher %s)" % __version__)
    parser.add_argument("--version", action="version", version="pypatcher " + __version__)
    sub = parser.add_subparsers(dest="command", metavar="BEFEHL")
    sub.required = True

    def add(name, func, help_text, parents=(common,)):
        p = sub.add_parser(name, parents=list(parents), help=help_text, description=help_text)
        p.set_defaults(func=func)
        return p

    p = add("init", cmd_init, "Journal anlegen, Ist-Stand als Ausgangszustand", (common, project))
    p.add_argument("--force", action="store_true",
                   help="bestehendes Journal neu anlegen (nur ohne angewendete Patches)")
    add("status", cmd_status, "Ueberblick ueber Zustand und Patches", (common, project))
    add("verify", cmd_verify, "Ist-Stand gegen das Journal pruefen", (common, project))
    add("log", cmd_log, "Historie anzeigen", (common, project))
    p = add("units", cmd_units, "Einheiten einer Datei mit Hashes")
    p.add_argument("file")
    p.add_argument("--compare", help='Profil, z.B. "python" oder "text,whitespace=keep"')
    p = add("bundle", cmd_bundle, "pypatcher als eine Datei (patcher.pyz) erzeugen")
    p.add_argument("-o", "--output", default="patcher.pyz", help="Ausgabe (Standard patcher.pyz)")
    p = add("pack", cmd_pack, "Patch/Entwurf umpacken: komprimieren, entpacken, teilen")
    p.add_argument("patch", help="Patch oder Entwurf (bei Paketen: ein beliebiges)")
    p.add_argument("-o", "--output", required=True, help="Ausgabe *.ppatch")
    p.add_argument("--compress", action="store_true", help="Datenbloecke komprimieren")
    p.add_argument("--max-size", type=int, help="in Pakete mit hoechstens N Zeichen teilen")
    def test_options(p):
        p.add_argument("--test", metavar="BEFEHL",
                       help='nach dem Anwenden ausfuehren, z.B. "python3 -m unittest" '
                       '(Vorgabe: "test" aus .patcher/config.json)')
        p.add_argument("--no-test", action="store_true", help="keine Tests ausfuehren")
        p.add_argument("--rollback-on-failure", action="store_true",
                       help="bei fehlgeschlagenen Tests automatisch zuruecknehmen")

    p = add("run", cmd_run, "Entwurf (z.B. von einer KI) direkt pruefen und anwenden",
            (common, project))
    p.add_argument("patch", help="Patch/Entwurf; Teile duerfen in einer Datei stehen")
    p.add_argument("-n", "--dry-run", action="store_true", help="nur pruefen, nichts aendern")
    p.add_argument("--set", action="append", metavar="NAME=WERT",
                   help="Antwort auf eine INPUT-Frage vorgeben (mehrfach moeglich)")
    p.add_argument("--answers", metavar="DATEI",
                   help="Antworten aus Datei (NAME=WERT je Zeile oder JSON-Objekt)")
    test_options(p)
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
        p.add_argument("--compress", action="store_true",
                       help="Datenbloecke komprimieren (zlib + Base64, ca. 55%% weniger "
                       "Zeichen; Befehlsliste bleibt lesbar)")

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
        p.add_argument("--set", action="append", metavar="NAME=WERT",
                       help="Antwort auf eine INPUT-Frage vorgeben (mehrfach moeglich)")
        p.add_argument("--answers", metavar="DATEI",
                       help="Antworten aus Datei (NAME=WERT je Zeile oder JSON-Objekt)")
        if name == "apply":
            test_options(p)
    p = add("rollback", cmd_rollback, "Patches zuruecknehmen", (common, project))
    p.add_argument("--to", metavar="ID", help="alle Patches nach ID zuruecknehmen")
    add("recover", cmd_recover, "Unterbrochene Transaktion bereinigen", (common, project))
    p = add("help", None, "Hilfe anzeigen (help BEFEHL: Hilfe zu einem Befehl)")
    p.add_argument("topic", nargs="?", metavar="BEFEHL")
    p.set_defaults(func=lambda args: _help(parser, sub, args.topic))
    return parser


def _help(parser, sub, topic):
    if topic is None:
        parser.print_help()
    elif topic in sub.choices:
        sub.choices[topic].print_help()
    else:
        raise UsageError("unbekannter Befehl %r - Befehle: %s" % (topic, ", ".join(sub.choices)))
    return 0


def main(argv=None):
    global REPORT
    args = build_parser().parse_args(argv)
    if not getattr(args, "json", False):
        REPORT = None
        return _main(args)
    REPORT = {"format": JSON_FORMAT, "version": __version__, "command": args.command}
    try:
        code = _main(args)
        REPORT["exit_code"] = code
        REPORT["ok"] = code == 0
        REPORT["result"] = RESULT_CODES.get(code, "ERROR")
        sys.stdout.write(json.dumps(REPORT, ensure_ascii=False, sort_keys=True) + "\n")
        return code
    finally:
        REPORT = None


def _main(args):
    global QUIET
    QUIET = args.quiet
    console.MODE = args.color
    try:
        return args.func(args)
    except flow.FlowExit as exc:
        report(error={"code": "EXIT", "message": str(exc), "path": None})
        say("ABBRUCH: %s - nichts geaendert." % exc, sys.stderr)
        return 1
    except PatcherError as exc:
        report(error={"code": RESULT_CODES.get(exc.exit_code, "ERROR"), "message": str(exc),
                      "path": getattr(exc, "path", None), "change": getattr(exc, "change", None),
                      "detail": getattr(exc, "detail", None)})
        say("FEHLER: %s" % exc, sys.stderr)
        return exc.exit_code
    except KeyboardInterrupt:
        print(console.paint("Abgebrochen.", "warn", sys.stderr), file=sys.stderr)
        return 1
    except BrokenPipeError:            # Ausgabe z.B. an "head" weitergeleitet
        try:
            sys.stdout = open(os.devnull, "w")
        except OSError:
            pass
        return 0
    except Exception as exc:  # interner Fehler
        if args.verbose:
            raise
        report(error={"code": "INTERNAL", "message": "%s: %s" % (type(exc).__name__, exc),
                      "path": None})
        say("INTERNER FEHLER: %s: %s (Details mit -v)" % (type(exc).__name__, exc), sys.stderr)
        return 5
