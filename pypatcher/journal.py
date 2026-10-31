"""Journal .patcher/ - Zustand, Historie, Sicherung, Rollback, Recover
(manifest.txt Abschnitte 11 und 12)."""

import datetime
import fnmatch
import json
import os
import shutil

from . import adapters, fileio, patchfile, profile as pf
from .errors import Conflict, JournalError
from .hashing import sha256_bytes, state_hash

JOURNAL_DIR = ".patcher"
# .patcherignore gehoert zur Umgebung und wird nie verwaltet; Systemdateien von
# macOS/Windows und Patchdateien im Projekt ebenso (abschaltbar mit "!muster")
DEFAULT_IGNORE = [".git", "__pycache__", "*.pyc", ".pptmp-*", ".patcherignore",
                  ".DS_Store", "Thumbs.db", "desktop.ini", "*.ppatch"]
FIXED_IGNORE = (".patcherignore",)
DEFAULT_CONFIG = {"ignore": [], "compare": {}, "require": {}}


def now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def read_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError:
        return default
    except (OSError, ValueError) as exc:
        raise JournalError("%s: nicht lesbar (%s)" % (path, exc))


def write_json(path, data):
    fileio.atomic_write(path, (json.dumps(data, indent=2, ensure_ascii=False) + "\n").encode("utf-8"))


def ignore_rules(root, config=None):
    """Alle Regeln mit Herkunft: [(muster, herkunft)], "!muster" eingeschlossen."""
    rules = [(p, "config.json") for p in (config or {}).get("ignore", [])]
    try:
        with open(os.path.join(root, ".patcherignore"), encoding="utf-8") as handle:
            for n, line in enumerate(handle, 1):
                line = line.strip()
                if line and not line.startswith("#"):
                    rules.append((line, ".patcherignore Zeile %d" % n))
    except FileNotFoundError:
        pass
    return rules


def ignore_patterns(root, config=None):
    """Muster aus Vorgaben, config.json ("ignore") und .patcherignore.
    "!muster" nimmt ein Vorgabe-Muster zurueck (z.B. "!*.ppatch")."""
    own = [p for p, _ in ignore_rules(root, config)]
    dropped = set(p[1:] for p in own if p.startswith("!")) - set(FIXED_IGNORE)
    return [p for p in DEFAULT_IGNORE if p not in dropped] + \
        [p for p in own if not p.startswith("!")]


def matches(rel, pattern):
    """Muster passt auf den ganzen Pfad oder einen Pfadteil; "name/" (wie in
    .gitignore) passt auf Verzeichnisse dieses Namens bzw. Pfads."""
    parts = rel.split("/")
    if pattern.endswith("/"):
        pattern = pattern.rstrip("/")
        return any(fnmatch.fnmatch("/".join(parts[:i]), pattern) or
                   fnmatch.fnmatch(parts[i - 1], pattern) for i in range(1, len(parts)))
    return fnmatch.fnmatch(rel, pattern) or any(fnmatch.fnmatch(part, pattern) for part in parts)


def explain_ignore(rel, root, config=None):
    """Warum wird rel ignoriert? -> (treffer, zurueckgenommen): je eine Liste
    [(muster, herkunft)] - treffer leer = nicht ignoriert; zurueckgenommen
    sind "!muster", die eine passende Vorgabe aufheben."""
    if rel.split("/")[0] == JOURNAL_DIR:
        return [(JOURNAL_DIR, "fest (Journal)")], []
    active = set(ignore_patterns(root, config))
    rules = ignore_rules(root, config)
    hits = [(p, "fest (Vorgabe)" if p in FIXED_IGNORE else "Vorgabe")
            for p in DEFAULT_IGNORE if p in active and matches(rel, p)]
    hits += [(p, src) for p, src in rules if not p.startswith("!") and matches(rel, p)]
    revoked = [(p, src) for p, src in rules if p.startswith("!") and p[1:] in DEFAULT_IGNORE
               and p[1:] not in FIXED_IGNORE and matches(rel, p[1:])]
    return hits, revoked


def is_ignored(rel, patterns):
    if rel.split("/")[0] == JOURNAL_DIR:
        return True
    return any(matches(rel, p) for p in patterns)


def _managed(files, patterns):
    """Verwaltete Dateien nach den aktuellen Ignore-Regeln -> (behalten, entfallen)."""
    keep, dropped = {}, []
    for rel, fp in files.items():
        if is_ignored(rel, patterns):
            dropped.append(rel)
        else:
            keep[rel] = fp
    return keep, sorted(dropped)


def scan(root, patterns):
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = os.path.relpath(dirpath, root).replace(os.sep, "/")
        rel_dir = "" if rel_dir == "." else rel_dir + "/"
        dirnames[:] = sorted(d for d in dirnames if not is_ignored(rel_dir + d, patterns))
        out.extend(rel_dir + f for f in filenames if not is_ignored(rel_dir + f, patterns))
    return sorted(out)


def auto_profile(rel, data, hints=None):
    """Vorgabe-Profil fuer verwaltete Dateien (init/verify/Zustand)."""
    if b"\0" in data:
        return pf.complete({"mode": "raw"})
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return pf.complete({"mode": "raw"})
    mode = pf.default_mode(rel)
    if mode != "text":
        adapter = adapters.get(mode)
        try:
            adapter.validate(fileio.decode(data, "utf-8", rel, True))
        except (adapters.ParseError, Conflict):
            if hints is not None:
                hints.append("%s: kein gueltiges %s - als text verwaltet" % (rel, adapter.label))
            mode = "text"
    return pf.complete({"mode": mode})


def fingerprint(rel, data, prof):
    doc = fileio.load_bytes(data, rel, prof)
    return {"profile": prof, "hash": adapters.file_hash(doc, prof),
            "text_hash": adapters.text_hash(doc), "raw": sha256_bytes(data)}


def project_hashes(root, patterns, overlay=None, overlay_profiles=None):
    """Funktions-Hashes aller Dateien; overlay = Stand im Speicher (Pfad -> Doc/None)."""
    overlay = overlay or {}
    hashes = {}
    for rel in scan(root, patterns):
        if rel in overlay:
            continue
        data = fileio.read_bytes(os.path.join(root, *rel.split("/")))
        prof = auto_profile(rel, data)
        hashes[rel] = adapters.file_hash(fileio.load_bytes(data, rel, prof), prof)
    for rel, doc in overlay.items():
        if doc is not None and not is_ignored(rel, patterns):
            hashes[rel] = adapters.file_hash(doc, overlay_profiles[rel])
    return hashes


class Journal(object):
    def __init__(self, root):
        self.root = os.path.abspath(root)
        self.unmanaged = []     # neue, nicht verwaltete Dateien (letztes verify)
        self.dir = os.path.join(self.root, JOURNAL_DIR)

    def path(self, *parts):
        return os.path.join(self.dir, *parts)

    def file(self, rel):
        return os.path.join(self.root, *rel.split("/"))

    @property
    def exists(self):
        return os.path.isfile(self.path("state.json"))

    def require(self):
        if not self.exists:
            raise JournalError("%s: kein Journal - zuerst 'patcher.py init'" % self.root)

    def config(self):
        return read_json(self.path("config.json"), {}) or {}

    def patterns(self):
        return ignore_patterns(self.root, self.config())

    def state(self, dropped=None):
        """Zustand nach den aktuellen Ignore-Regeln: neu ignorierte Dateien
        fallen aus der Verwaltung (dropped: Liste fuer deren Namen)."""
        state = read_json(self.path("state.json"), {"files": {}})
        files, gone = _managed(state["files"], self.patterns())
        if gone:
            state = {"files": files, "state_hash": state_hash(
                dict((rel, fp["hash"]) for rel, fp in files.items()))}
            if dropped is not None:
                dropped.extend(gone)
        return state

    def history(self):
        return read_json(self.path("history.json"), {"applied": [], "log": []})

    def txn(self):
        return read_json(self.path("txn.json"))

    def require_no_txn(self):
        if self.txn():
            raise JournalError("unterbrochene Transaktion gefunden - 'patcher.py recover' ausfuehren")

    def applied_ids(self):
        return [e["id"] for e in self.history()["applied"]]

    def _save_state(self, files):
        hashes = dict((rel, fp["hash"]) for rel, fp in files.items())
        write_json(self.path("state.json"), {"files": files, "state_hash": state_hash(hashes)})

    # -- init --------------------------------------------------------------

    def init(self, force=False):
        if self.exists:
            if not force:
                raise JournalError("%s: Journal existiert bereits (neu anlegen: init --force)"
                                   % self.root)
            self.require_no_txn()
            applied = self.applied_ids()
            if applied:
                raise JournalError("init --force nur ohne angewendete Patches (%d angewendet, "
                                   "zuerst 'patcher.py rollback --to' bzw. rollback)" % len(applied))
            for name in ("patches", "backups"):
                shutil.rmtree(self.path(name), ignore_errors=True)
        os.makedirs(self.path("patches"), exist_ok=True)
        os.makedirs(self.path("backups"), exist_ok=True)
        if not os.path.isfile(self.path("config.json")):
            write_json(self.path("config.json"), DEFAULT_CONFIG)
        hints = []
        files = {}
        for rel in scan(self.root, self.patterns()):
            data = fileio.read_bytes(self.file(rel))
            prof = auto_profile(rel, data, hints)
            try:
                files[rel] = fingerprint(rel, data, prof)
            except Conflict as exc:
                hints.append("%s - als raw verwaltet" % exc)
                files[rel] = fingerprint(rel, data, pf.complete({"mode": "raw"}))
        write_json(self.path("history.json"),
                   {"applied": [], "log": [{"time": now(), "action": "init"}]})
        self._save_state(files)
        return files, hints

    # -- apply -------------------------------------------------------------

    def commit_apply(self, patch, ctx, answers=None):
        pid = patch["id"]
        touched = sorted(ctx.touched())
        bdir = self.path("backups", pid)
        if os.path.isdir(bdir):
            shutil.rmtree(bdir)
        meta = {}
        for rel in touched:
            src = self.file(rel)
            meta[rel] = os.path.isfile(src)
            if meta[rel]:
                dst = os.path.join(bdir, "files", *rel.split("/"))
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copy2(src, dst)
        write_json(os.path.join(bdir, "meta.json"), {"files": meta})
        write_json(self.path("txn.json"), {"kind": "apply", "id": pid, "phase": "write",
                                           "files": meta, "time": now()})
        written = {}
        try:
            for rel in touched:
                doc = ctx.docs[rel]
                target = self.file(rel)
                if doc is None:
                    if os.path.isfile(target):
                        os.remove(target)
                        fileio.drop_bytecode(target)
                        self._prune_dirs(os.path.dirname(target))
                    continue
                data = fileio.encode(doc)
                fileio.atomic_write(target, data, doc.st_mode)
                written[rel] = data
        except BaseException as exc:
            self.restore(pid, meta)
            os.remove(self.path("txn.json"))
            raise JournalError("Schreiben fehlgeschlagen, Ausgangsstand wiederhergestellt: %s" % exc)
        write_json(self.path("txn.json"), {"kind": "apply", "id": pid, "phase": "journal",
                                           "files": meta, "time": now()})
        fileio.atomic_write(self.path("patches", pid + patchfile.EXT),
                            patchfile.dumps(patch).encode("utf-8"))
        state = self.state()
        files_info = {}
        for rel in touched:
            before = None
            if meta[rel]:
                before = state["files"].get(rel)
                if before is None:
                    data = fileio.read_bytes(os.path.join(bdir, "files", *rel.split("/")))
                    before = fingerprint(rel, data, ctx.profiles[rel])
            after = fingerprint(rel, written[rel], ctx.profiles[rel]) if rel in written else None
            files_info[rel] = {"before": before, "after": after}
        history = self.history()
        history["applied"].append({"id": pid, "title": patch.get("title", ""),
                                   "patch_hash": patch.get("patch_hash"),
                                   "applied_at": now(), "files": files_info,
                                   "answers": answers or {}})
        history["log"].append({"time": now(), "action": "apply", "id": pid})
        write_json(self.path("history.json"), history)
        self._sync_state(files_info, "after")
        os.remove(self.path("txn.json"))
        return files_info

    def _sync_state(self, files_info, side):
        """state.json nach einem Historien-Eintrag richten (wiederholbar - auch
        nach einem Abbruch zwischen history.json und state.json)."""
        state = self.state()
        for rel, fps in files_info.items():
            if fps[side] is None:
                state["files"].pop(rel, None)
            else:
                state["files"][rel] = fps[side]
        self._save_state(state["files"])

    # -- rollback / recover ------------------------------------------------

    def restore(self, pid, meta):
        bdir = self.path("backups", pid, "files")
        for rel, existed in meta.items():
            target = self.file(rel)
            if existed:
                src = os.path.join(bdir, *rel.split("/"))
                fileio.atomic_write(target, fileio.read_bytes(src), os.stat(src).st_mode)
            elif os.path.isfile(target):
                os.remove(target)
                fileio.drop_bytecode(target)
                self._prune_dirs(os.path.dirname(target))

    def _prune_dirs(self, directory):
        """Durch den Patch entstandene, jetzt leere Verzeichnisse entfernen."""
        while os.path.normcase(directory) != os.path.normcase(self.root) and \
                directory.startswith(self.root) and os.path.isdir(directory) and \
                not os.path.islink(directory) and not os.listdir(directory):
            os.rmdir(directory)
            directory = os.path.dirname(directory)

    def _backup_meta(self, pid):
        meta = read_json(self.path("backups", pid, "meta.json"))
        if meta is None:
            raise JournalError("Sicherung fuer %s fehlt" % pid)
        return meta["files"]

    def check_after(self, entry):
        """Ist-Stand muss gemaess Profil dem Stand nach dem Patch entsprechen."""
        problems = []
        for rel, fps in sorted(entry["files"].items()):
            after = fps["after"]
            target = self.file(rel)
            if after is None:
                if os.path.exists(target):
                    problems.append("%s: sollte nicht existieren" % rel)
                continue
            if not os.path.isfile(target):
                problems.append("%s: fehlt" % rel)
                continue
            try:
                fp = fingerprint(rel, fileio.read_bytes(target), after["profile"])
            except Exception as exc:  # nicht lesbar/parsebar = funktional veraendert
                problems.append("%s: %s" % (rel, exc))
                continue
            if fp["hash"] != after["hash"]:
                problems.append("%s: seit dem Patch funktional veraendert" % rel)
        if problems:
            raise Conflict("Rollback von %s nicht moeglich:\n  " % entry["id"] + "\n  ".join(problems))

    def rollback_one(self, entry):
        pid = entry["id"]
        meta = self._backup_meta(pid)
        self.check_after(entry)
        write_json(self.path("txn.json"), {"kind": "rollback", "id": pid, "phase": "write",
                                           "files": meta, "time": now(),
                                           "entry_files": entry["files"]})
        self.restore(pid, meta)
        self._finish_rollback(pid, entry["files"])

    def _finish_rollback(self, pid, files_info=None):
        history = self.history()
        entry = next((e for e in history["applied"] if e["id"] == pid), None)
        if entry is not None:
            files_info = entry["files"]
            history["applied"].remove(entry)
            history["log"].append({"time": now(), "action": "rollback", "id": pid})
            write_json(self.path("history.json"), history)
        if files_info is not None:
            self._sync_state(files_info, "before")
        for path in (self.path("patches", pid + patchfile.EXT),):
            if os.path.isfile(path):
                os.remove(path)
        shutil.rmtree(self.path("backups", pid), ignore_errors=True)
        if os.path.isfile(self.path("txn.json")):
            os.remove(self.path("txn.json"))

    def recover(self):
        txn = self.txn()
        if not txn:
            return None
        pid = txn["id"]
        if txn["kind"] == "apply":
            entry = next((e for e in self.history()["applied"] if e["id"] == pid), None)
            if entry is not None:
                self._sync_state(entry["files"], "after")
                os.remove(self.path("txn.json"))
                return "Patch %s war vollstaendig angewendet - Transaktion abgeschlossen" % pid
            self.restore(pid, txn["files"])
            shutil.rmtree(self.path("backups", pid), ignore_errors=True)
            patch_copy = self.path("patches", pid + patchfile.EXT)
            if os.path.isfile(patch_copy):
                os.remove(patch_copy)
            os.remove(self.path("txn.json"))
            return "Patch %s war unvollstaendig - Ausgangsstand wiederhergestellt" % pid
        self.restore(pid, txn["files"])
        self._finish_rollback(pid, txn.get("entry_files"))
        return "Rollback von %s abgeschlossen" % pid

    # -- verify ------------------------------------------------------------

    def verify(self):
        """-> (fehler, hinweise)."""
        errors, notes = [], []
        dropped = []
        files = self.state(dropped)["files"]
        for rel in dropped:
            notes.append("%s: wird jetzt ignoriert - nicht mehr verwaltet" % rel)
        present = set(scan(self.root, self.patterns()))
        for rel, fp in sorted(files.items()):
            if rel not in present:
                errors.append("%s: fehlt" % rel)
                continue
            data = fileio.read_bytes(self.file(rel))
            if sha256_bytes(data) == fp["raw"]:
                continue
            try:
                now_fp = fingerprint(rel, data, fp["profile"])
            except Exception as exc:
                errors.append("%s: funktional veraendert (%s)" % (rel, exc))
                continue
            if now_fp["hash"] == fp["hash"]:
                notes.append("%s: nur kosmetisch veraendert (Kommentare/Leerraum/Zeilenenden)" % rel)
            else:
                errors.append("%s: funktional veraendert" % rel)
        # Neue, nicht verwaltete Dateien widersprechen dem Journal nicht (z.B.
        # heruntergeladene Hilfsdateien) - Hinweis statt Fehler
        new = self.unmanaged = sorted(present - set(files))
        for rel in new:
            notes.append("%s: neue Datei, nicht verwaltet (ignorieren: .patcherignore; "
                         "aufnehmen: per Patch REP oder - ohne angewendete Patches - "
                         "init --force)" % rel)
        return errors, notes
