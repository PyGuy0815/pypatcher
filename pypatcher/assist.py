"""Hilfen fuer die Arbeit mit einer KI im Chat (0.0.4): Zwischenablage,
Handshake, Projektueberblick und Abfragen (tree, find, source) sowie der
Fehlerbericht nach einem gescheiterten Patch (report).

Alle Abfragen lesen nur; sie beachten die Ignore-Regeln, verlassen das
Projekt nicht (keine symbolischen Links, kein .git/.patcher) und kuerzen
lange Ausgaben.
"""

import json
import os
import pkgutil
import shutil
import subprocess
import sys

from . import __version__, adapters, fileio, journal as jr, patchfile
from . import profile as pf
from .adapters import ParseError
from .errors import Conflict, PatcherError, PatchInvalid, UsageError
from .hashing import sha256_text


class EnvironmentProblem(PatcherError):
    """Werkzeug der Umgebung fehlt (z.B. Zugriff auf die Zwischenablage)."""
    exit_code = 7


# -- Zwischenablage ------------------------------------------------------------

PASTE = {
    "darwin": [["pbpaste"]],
    "win32": [["powershell", "-NoProfile", "-Command",
               "[Console]::OutputEncoding=[Text.Encoding]::UTF8; Get-Clipboard -Raw"]],
    "linux": [["wl-paste", "--no-newline"], ["xclip", "-selection", "clipboard", "-o"],
              ["xsel", "--clipboard", "--output"]],
}
COPY = {
    "darwin": [["pbcopy"]],
    "win32": [["powershell", "-NoProfile", "-Command",
               "[Console]::InputEncoding=[Text.Encoding]::UTF8; "
               "Set-Clipboard -Value ([Console]::In.ReadToEnd())"]],
    "linux": [["wl-copy"], ["xclip", "-selection", "clipboard"],
              ["xsel", "--clipboard", "--input"]],
}


def _platform():
    if sys.platform.startswith("linux") or sys.platform.startswith("freebsd"):
        return "linux"
    return sys.platform


def _tools(table):
    tools = [t for t in table.get(_platform(), []) if shutil.which(t[0])]
    if not tools:
        names = ", ".join(t[0] for t in table.get(_platform(), [])) or "-"
        raise EnvironmentProblem(
            "kein Zugriff auf die Zwischenablage (gesucht: %s) - Patch stattdessen in eine "
            "Datei speichern oder mit '-' ueber die Eingabe uebergeben" % names)
    return tools


def paste():
    """Text der Zwischenablage."""
    errors = []
    for tool in _tools(PASTE):
        try:
            result = subprocess.run(tool, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, timeout=10)
        except (OSError, subprocess.SubprocessError) as exc:
            errors.append("%s: %s" % (tool[0], exc))
            continue
        if result.returncode == 0:
            return result.stdout.decode("utf-8", "replace").replace("\r\n", "\n")
        errors.append("%s: %s" % (tool[0], result.stderr.decode("utf-8", "replace").strip()))
    raise EnvironmentProblem("Zwischenablage nicht lesbar (%s)" % "; ".join(errors))


def copy(text):
    errors = []
    for tool in _tools(COPY):
        try:
            result = subprocess.run(tool, input=text.encode("utf-8"), stdout=subprocess.DEVNULL,
                                    stderr=subprocess.PIPE, timeout=10)
        except (OSError, subprocess.SubprocessError) as exc:
            errors.append("%s: %s" % (tool[0], exc))
            continue
        if result.returncode == 0:
            return
        errors.append("%s: %s" % (tool[0], result.stderr.decode("utf-8", "replace").strip()))
    raise EnvironmentProblem("Zwischenablage nicht beschreibbar (%s)" % "; ".join(errors))


# -- Projektdateien ------------------------------------------------------------

FENCE_LANG = {"python": "python", "js": "javascript", "ts": "typescript", "tsx": "tsx",
              "jsx": "jsx", "css": "css", "scss": "scss", "html": "html", "xml": "xml",
              "c": "c", "cpp": "cpp", "java": "java", "json": "json", "jsonc": "jsonc",
              "lua": "lua", "vb": "vbnet", "vba": "vb"}
MAX_LINES = 400         # je Datei/Einheit bei source
MAX_UNITS = 40          # je Datei bei context


class Project(object):
    """Lesender Zugriff auf ein Projekt (mit oder ohne Journal)."""

    def __init__(self, root):
        self.journal = jr.Journal(root)
        self.root = self.journal.root
        if not os.path.isdir(self.root):
            raise UsageError("%s: kein Verzeichnis" % root)
        self.patterns = self.journal.patterns()
        self._files = None

    def files(self):
        if self._files is None:
            self._files = [rel for rel in jr.scan(self.root, self.patterns)
                           if not self._linked(rel)]
        return self._files

    def _linked(self, rel):
        parts = rel.split("/")
        return any(os.path.islink(os.path.join(self.root, *parts[:i]))
                   for i in range(1, len(parts) + 1))

    def rel(self, path):
        """Pfad von der Kommandozeile (relativ zum Projekt) pruefen."""
        try:
            rel = patchfile.norm_path(path.replace(os.sep, "/").rstrip("/"))
        except PatchInvalid as exc:
            raise UsageError(str(exc))
        if self._linked(rel):
            raise UsageError("%s: Pfad fuehrt ueber einen symbolischen Link - nicht erlaubt" % rel)
        return rel

    def load(self, rel):
        """-> (doc, prof, info oder None, Fehlertext oder None)."""
        path = os.path.join(self.root, *rel.split("/"))
        if not os.path.isfile(path):
            raise UsageError("%s: Datei nicht gefunden" % rel)
        data = fileio.read_bytes(path)
        compare = pf.lookup(self.journal.config().get("compare"), rel)
        prof = pf.resolve(rel, None, None, compare) if compare else jr.auto_profile(rel, data)
        doc = fileio.load_bytes(data, rel, prof)
        ua = adapters.get(prof["mode"])
        if prof["mode"] == "raw" or not ua.has_units:
            return doc, prof, None, None
        try:
            return doc, prof, ua.analyze(doc), None
        except (ParseError, Conflict) as exc:
            return doc, prof, None, "kein gueltiges %s (%s)" % (ua.label, exc)


def _fence(lines, mode):
    longest = max([len(l) - len(l.lstrip("`")) for l in lines] + [0])
    fence = "`" * max(3, longest + 1)
    return [fence + FENCE_LANG.get(mode, "")] + lines + [fence]


def _units(info):
    return [u for u in info.units if not u.path.startswith("@")]


def _span(u):
    return "%d-%d" % (u.start + 1, u.end) if u.end > u.start else "-"


# -- Abfragen --------------------------------------------------------------------

def tree(project, paths):
    prefixes = [project.rel(p) for p in paths] or [""]
    files = [f for f in project.files()
             if any(not p or f == p or f.startswith(p + "/") for p in prefixes)]
    if not files:
        raise UsageError("keine (nicht ignorierten) Dateien unter %s" % ", ".join(paths or ["."]))
    lines, shown = [], set()
    for rel in files:
        parts = rel.split("/")
        for depth in range(len(parts) - 1):
            key = "/".join(parts[:depth + 1])
            if key not in shown:
                shown.add(key)
                lines.append("  " * depth + parts[depth] + "/")
        lines.append("  " * (len(parts) - 1) + parts[-1])
    return lines + ["(%d Dateien; ignorierte nicht aufgefuehrt)" % len(files)]


def find(project, names):
    out = []
    for name in names:
        low, hits = name.lower(), []
        for rel in project.files():
            if rel.split("/")[-1] == name:
                hits.append("%s  (Datei)" % rel)
            try:
                _, _, info, _ = project.load(rel)
            except (PatcherError, OSError):
                continue
            for u in _units(info) if info else []:
                last = u.path.replace("::", ".").split(".")[-1].split("/")[-1]
                if u.path == name or last == name or u.path.endswith(("." + name, "::" + name,
                                                                       "/" + name)):
                    hits.append("%s::%s  (%s, Zeilen %s)" % (rel, u.path, u.kind, _span(u)))
                elif last.lower() == low:
                    hits.append("%s::%s  (%s, Zeilen %s; Schreibweise abweichend)"
                                % (rel, u.path, u.kind, _span(u)))
        out.append("%s: %s" % (name, "%d Treffer" % len(hits) if hits else "nicht gefunden"))
        out.extend("  " + h for h in hits)
    return out


def _split_target(target):
    """"datei", "datei::einheit" oder "datei:von-bis"."""
    if "::" in target:
        path, unit = target.split("::", 1)
        return path, unit, None
    path, sep, span = target.rpartition(":")
    if sep and path and span.replace("-", "").isdigit():
        lo, _, hi = span.partition("-")
        return path, None, (int(lo), int(hi or lo))
    return target, None, None


def source(project, targets, max_lines=MAX_LINES):
    out = []
    for target in targets:
        path, unit, span = _split_target(target)
        rel = project.rel(path)
        doc, prof, info, problem = project.load(rel)
        if prof["mode"] == "raw":
            out += ["=== %s ===" % rel, "(Binaerdatei, %d Bytes - kein Text)" % len(doc.raw), ""]
            continue
        lines = doc.lines
        if unit:
            if info is None:
                raise Conflict("%s: Einheiten nicht verfuegbar (%s)"
                               % (rel, problem or "mode %s ohne Einheiten" % prof["mode"]))
            u = adapters.get(prof["mode"]).find(info, unit)
            title, start, end = "%s::%s (%s, Zeilen %s)" % (rel, u.path, u.kind, _span(u)), \
                u.start, u.end
        elif span:
            start, end = max(span[0] - 1, 0), min(span[1], len(lines))
            title = "%s (Zeilen %d-%d von %d)" % (rel, start + 1, end, len(lines))
        else:
            start, end = 0, len(lines)
            title = "%s (%d Zeilen, %s)" % (rel, len(lines), pf.profile_id(prof))
        body = lines[start:end]
        cut = len(body) > max_lines
        out += ["=== %s ===" % title] + _fence(body[:max_lines], prof["mode"])
        if cut:
            out.append("(gekuerzt: %d von %d Zeilen - Rest mit %s:%d-%d)"
                       % (max_lines, len(body), rel, start + max_lines + 1, end))
        out.append("")
    return out[:-1] if out and out[-1] == "" else out


def context(project, paths):
    j = project.journal
    lines = ["PYPATCH-CONTEXT/1",
             "pypatcher %s | Format pypatcher/1 | Projekt: %s"
             % (__version__, os.path.basename(project.root) or project.root)]
    if j.exists:
        applied = j.history()["applied"]
        errors, _ = j.verify()
        lines.append("Journal: %d Patches angewendet%s, Ist-Stand %s" % (
            len(applied), " (letzter: %s)" % applied[-1]["id"] if applied else "",
            "OK" if not errors else "mit %d Abweichung(en)" % len(errors)))
    else:
        lines.append("Journal: keines (vor dem ersten Patch: init)")
    test = j.config().get("test") if j.exists else None
    if test:
        lines.append("Test: %s" % test)
    prefixes = [project.rel(p) for p in paths]
    files = [f for f in project.files()
             if not prefixes or any(f == p or f.startswith(p + "/") for p in prefixes)]
    lines.append("Dateien (%d, ohne ignorierte; Einheiten in [...]):" % len(files))
    for rel in files:
        try:
            doc, prof, info, problem = project.load(rel)
        except (PatcherError, OSError) as exc:
            lines.append("  %s  (nicht lesbar: %s)" % (rel, exc))
            continue
        size = "%d Bytes" % len(doc.raw) if prof["mode"] == "raw" else "%d Zeilen" % len(doc.lines)
        line = "  %s  %s, %s" % (rel, prof["mode"], size)
        if problem:
            line += "  (%s)" % problem
        elif info is not None:
            names = [u.path for u in _units(info)]
            more = len(names) - MAX_UNITS
            if names:
                line += "  [%s%s]" % (", ".join(names[:MAX_UNITS]),
                                      ", ... +%d (alle: find/units)" % more if more > 0 else "")
        lines.append(line)
    return lines


# -- Handshake -------------------------------------------------------------------

def _prompt_text():
    data = None
    try:
        data = pkgutil.get_data("pypatcher", "ki-prompt.txt")       # Einzeldatei (bundle)
    except OSError:
        pass
    if data is None:
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "ki-prompt.txt")
        try:
            with open(path, "rb") as handle:
                data = handle.read()
        except OSError:
            raise EnvironmentProblem("ki-prompt.txt nicht gefunden (gehoert neben patcher.py)")
    text = data.decode("utf-8").replace("\r\n", "\n")
    start = text.find("\n", text.find("--- ab hier")) + 1
    end = text.find("------", start)
    return text[start:end if end > 0 else len(text)].rstrip() + "\n"


def invocation():
    """So ruft der Nutzer pypatcher auf (fuer Befehle, die die KI nennt)."""
    prog = sys.argv[0] if sys.argv and sys.argv[0].endswith((".py", ".pyz")) else "patcher.py"
    if os.path.isabs(prog):
        try:
            rel = os.path.relpath(prog)
            if not rel.startswith(".."):        # unterhalb des aktuellen Ordners: kurz
                prog = rel
        except ValueError:                      # anderes Laufwerk (Windows)
            pass
    return "python3 " + prog.replace(os.sep, "/")


def _drop_section(text, number):
    start = text.find("\n%d. " % number)
    if start < 0:
        return text
    end = text.find("\n%d. " % (number + 1), start)
    return text[:start + 1] + (text[end + 1:] if end > 0 else "")


def handshake(project, project_arg, full=False):
    prompt = _prompt_text()
    spec = patchfile.short(sha256_text(prompt), 16)
    if not full:
        prompt = _drop_section(prompt, 10)          # Sandbox-Teil: nur mit --full
    call = invocation()
    target = project_arg if project_arg not in (None, "") else "."
    prompt = prompt.replace("python3 patcher.pyz", call).replace("python3 patcher.py", call)
    prompt = prompt.replace("<projekt>", target)
    lines = ["PYPATCH-HANDSHAKE/1",
             "pypatcher %s | Format pypatcher/1 | Spezifikation %s" % (__version__, spec),
             "Aufruf beim Nutzer: %s BEFEHL ... -p %s" % (call, target)]
    if project is not None and project.journal.exists:
        modes = {}
        for rel in project.files():
            mode = pf.default_mode(rel)
            modes[mode] = modes.get(mode, 0) + 1
        test = project.journal.config().get("test")
        lines.append("Projekt: %d Dateien (%s)%s" % (
            len(project.files()), ", ".join("%s %d" % kv for kv in sorted(modes.items())),
            "; Test: %s" % test if test else ""))
    lines += ["", prompt.rstrip(), "",
              "12. ARBEITSWEISE IN DIESEM CHAT (ohne Dateiaustausch)",
              "   Du kannst die Dateien des Nutzers nicht lesen. Fehlt dir Code, bitte",
              "   um GENAU EINEN Befehl; der Nutzer fuehrt ihn aus, die Ausgabe landet",
              "   in seiner Zwischenablage (--copy) und er fuegt sie hier ein:",
              "     %s context -p %s --copy            Ueberblick: Dateien, Einheiten"
              % (call, target),
              "     %s tree [PFAD] -p %s --copy        Ordnerstruktur" % (call, target),
              "     %s find NAME [NAME ...] -p %s --copy   Einheit/Datei suchen" % (call, target),
              "     %s source DATEI::EINHEIT [DATEI:10-40 ...] -p %s --copy" % (call, target),
              "   find/source nehmen mehrere Namen - alles Noetige in einen Befehl.",
              "   Den Patch gibst du in EINEM Codeblock aus; der Nutzer kopiert ihn und ruft",
              "     %s run --clipboard -p %s" % (call, target),
              "   auf. Schlaegt er fehl, schickt dir der Nutzer die Ausgabe von",
              "     %s report -p %s --copy" % (call, target),
              "   (Fehler, betroffene Stelle im aktuellen Stand); liefere dann einen",
              "   korrigierten Entwurf mit derselben id. Hashes schreibst du nie -",
              "   pypatcher versiegelt den Entwurf selbst gegen die Dateien des Nutzers.",
              "",
              "HANDSHAKE: Antworte jetzt nur mit",
              "  PYPATCH/1 BEREIT - Spezifikation %s" % spec,
              "und warte auf den Aenderungswunsch. Fordere Code mit den Befehlen aus",
              "Punkt 12 an, statt ihn zu erraten."]
    return lines


# -- Fehlerbericht ------------------------------------------------------------------

LAST_ERROR = "last-error.json"
LAST_PATCH = "last-error.ppatch"


def save_error(project_dir, command, patch_path, exc):
    """Nach einem gescheiterten check/apply/run: Fehler fuer 'report' merken
    (nur mit Journal; nie selbst ein Fehler)."""
    try:
        j = jr.Journal(project_dir)
        if not j.exists:
            return
        text = ""
        if patch_path and os.path.isfile(patch_path):
            with open(patch_path, encoding="utf-8", errors="replace") as handle:
                text = handle.read()
        fileio.atomic_write(j.path(LAST_PATCH), text.encode("utf-8"))
        fileio.atomic_write(j.path(LAST_ERROR), (json.dumps({
            "time": jr.now(), "command": command, "message": str(exc),
            "code": type(exc).__name__, "path": getattr(exc, "path", None),
            "change": getattr(exc, "change", None), "detail": getattr(exc, "detail", None),
        }, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    except Exception:
        pass


def clear_error(project_dir):
    j = jr.Journal(project_dir)
    for name in (LAST_ERROR, LAST_PATCH):
        try:
            os.remove(j.path(name))
        except OSError:
            pass


def _failed_change(patch_text, err):
    """Betroffene Aenderung aus dem gemerkten Patch: (entry, change) oder Nones."""
    if not patch_text or not err.get("path"):
        return None, None
    tmp = None
    try:
        import tempfile
        fd, tmp = tempfile.mkstemp(suffix=patchfile.EXT)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(patch_text)
        patch = patchfile.load(tmp, verify=False)
    except (PatcherError, OSError):
        return None, None
    finally:
        if tmp:
            os.remove(tmp)
    entry = next((e for e in patch["files"] if e.get("path") == err["path"]), None)
    change = None
    if entry is not None and err.get("change"):
        index = err["change"].get("index", 0) - 1
        changes = entry.get("changes") or []
        if 0 <= index < len(changes):
            change = changes[index]
    return entry, change


def report(project):
    j = project.journal
    j.require()
    err = jr.read_json(j.path(LAST_ERROR), None)
    if not err:
        return ["PYPATCH-REPORT/1", "Kein gemerkter Fehler - der letzte check/apply/run war "
                "erfolgreich (oder lief ohne Journal)."]
    try:
        with open(j.path(LAST_PATCH), encoding="utf-8") as handle:
            patch_text = handle.read()
    except OSError:
        patch_text = ""
    head = next((l for l in patch_text.split("\n") if l.startswith(patchfile.MAGIC + " ")), "")
    pid = head.split()[2] if len(head.split()) > 2 else "?"
    lines = ["PYPATCH-REPORT/1",
             "Patch: %s | Befehl: %s | %s" % (pid, err.get("command"), err.get("time")),
             "FEHLER: %s" % err.get("message"), ""]
    entry, change = _failed_change(patch_text, err)
    rel = err.get("path")
    if not rel:
        lines.append("Der Patch selbst ist fehlerhaft (Format) - Projektdateien wurden nicht "
                     "gelesen.")
    else:
        unit = change and (change.get("unit") or change.get("scope") or change.get("from"))
        if change:
            lines.append("Betroffen: %s, Aenderung %d (%s)%s" % (
                rel, err["change"]["index"], change.get("op"),
                " in %s" % unit if unit else ""))
        lines.append("Aktueller Stand beim Nutzer:")
        try:
            doc, prof, info, problem = project.load(project.rel(rel))
            if unit and info is not None:
                try:
                    lines += source(project, ["%s::%s" % (rel, unit)])
                except PatcherError:
                    unit = None
            if not unit or info is None:
                if prof["mode"] != "raw" and len(doc.lines) <= 2 * MAX_LINES // 3:
                    lines += source(project, [rel])
                else:
                    lines.append("=== %s (%s) ===" % (rel, "%d Zeilen" % len(doc.lines)
                                                     if prof["mode"] != "raw" else "binaer"))
                    if info is not None:
                        lines += ["  %s  (%s, Zeilen %s)" % (u.path, u.kind, _span(u))
                                  for u in _units(info)]
                    if problem:
                        lines.append("  (%s)" % problem)
                    lines.append("(Datei zu gross fuer den Bericht - Ausschnitt mit 'source "
                                 "%s:VON-BIS' anfordern)" % rel)
        except PatcherError as exc:
            lines.append("  (%s)" % exc)
    lines += ["", "Bitte einen korrigierten Entwurf mit derselben id liefern; Text in alt-/"
              "anker-Bloecken Zeichen fuer Zeichen aus dem aktuellen Stand oben."]
    return lines
