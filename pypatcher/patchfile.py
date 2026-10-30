"""Patchdatei pypatcher/1: Befehlsliste + Datenbloecke, optional in Pakete
geteilt (manifest.txt Abschnitt 6).

Intern wird ein Patch als dict dargestellt (Schluessel wie in Abschnitt 6
beschrieben); dieses Modul uebersetzt zwischen dict und Text.
"""

import base64
import binascii
import glob
import os
import re
import shlex
import zlib

from . import FORMAT
from .errors import PatchInvalid
from .fileio import atomic_write
from .hashing import sha256_text
from . import profile as pf

EXT = ".ppatch"
MAGIC = "#PPATCH"
FORMAT_VERSION = "1"
LEVELS = ("min", "std", "full")
DEFAULT_HASH_LEN = 16
MIN_HASH_LEN = 8
B64_WIDTH = 76

MODE_SHORT = {"python": "py", "text": "txt", "raw": "raw", "js": "js", "css": "css",
              "scss": "scss", "html": "html", "xml": "xml"}
MODE_LONG = dict((v, k) for k, v in MODE_SHORT.items())

FILE_CMDS = {"ADD": "add", "DEL": "delete", "REP": "replace", "MOVE": "move",
             "COPY": "copy", "FILE": "modify"}
CHANGE_CMDS = {"UNIT": "replace_unit", "UADD": "add_unit", "UDEL": "delete_unit",
               "UCOPY": "copy_unit", "UMOVE": "move_unit", "TEXT": "replace_text",
               "TINS": "insert_text", "TDEL": "delete_text"}
CMD_OF = dict((v, k) for k, v in list(FILE_CMDS.items()) + list(CHANGE_CMDS.items()))
ACTIONS = tuple(FILE_CMDS.values())
OPS = tuple(CHANGE_CMDS.values())
KEYS = ("h", "t", "u", "c", "s", "ind", "nl", "eof", "src", "as", "after", "to", "id",
        "scope")
META = ("TITLE", "DESC", "AUTHOR", "DATE", "GEN", "USES", "REQ", "MATCH", "DEFAULT", "STATE")
FLOW = ("PRINT", "INPUT", "IF", "ELIF", "ELSE", "END", "LABEL", "GOTO", "SET", "EXIT")
FENCE = "```"

ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
BLOCK_RE = re.compile(r"^@(\d+)(?:\s+(\d+|\.)(?:\s+([a-z0-9]+))?)?\s*$")
BLOCK_FLAGS = ("lit", "b64", "z64")
# Funktionen, die ein Patch ueber "USES" verlangen kann; aeltere Versionen
# brechen bei unbekannten Funktionen mit klarer Meldung ab (Abschnitt 6.3).
FEATURES = ("flow", "z64")
REF_RE = re.compile(r"^@(\d+)$")
POS_RE = re.compile(r"^(start|end|(after|before|start-of|end-of):.+)$")
PROFILE_RE = re.compile(r"^(py|txt|raw|js|css|scss|html|xml)(,[a-z_]+=[^,\s]*)*$")
PART_RE = re.compile(r"^(.*)\.(\d+)" + re.escape(EXT) + "$")


# -- Hilfen ------------------------------------------------------------------

def norm_path(path, what="Pfad"):
    """Relativer Projektpfad mit "/"; keine absoluten Pfade, kein "..", nicht .patcher."""
    if not isinstance(path, str) or not path.strip():
        raise PatchInvalid("%s fehlt" % what)
    p = path.replace("\\", "/")
    while p.startswith("./"):
        p = p[2:]
    parts = p.split("/")
    if (p.startswith("/") or re.match(r"^[A-Za-z]:", p)
            or any(part in ("", ".", "..") for part in parts) or parts[0] == ".patcher"):
        raise PatchInvalid("%s ungueltig: %r" % (what, path))
    return p


def short(value, length=None):
    """Hash ohne "sha256:"-Praefix, auf length Zeichen gekuerzt."""
    hexpart = value.split(":", 1)[-1]
    return hexpart[:length] if length else hexpart


def hash_match(stored, computed):
    """Gespeicherter (evtl. gekuerzter) Hash passt zum berechneten?"""
    stored = short(str(stored)).lower()
    return len(stored) >= MIN_HASH_LEN and short(computed).startswith(stored)


def _quote(token):
    if token and not re.search(r"[\s\"'\\#]", token):
        return token
    return '"' + token.replace("\\", "\\\\").replace('"', '\\"') + '"'


def profile_short(prof):
    """Profil als Kurzform; nur Abweichungen von den Vorgaben."""
    if not prof:
        return None
    mode = prof.get("mode", "text")
    options = pf.MODES[mode][1]
    opts = []
    for key, value in sorted(prof.items()):
        if key == "mode":
            continue
        allowed = options.get(key)
        default = allowed[0] if allowed else pf.FREE_DEFAULTS.get(key)
        if value != default:
            opts.append("%s=%s" % (key, "" if value is None else value))
    return ",".join([MODE_SHORT[mode]] + opts)


def profile_long(token):
    parts = token.split(",")
    prof = {"mode": MODE_LONG[parts[0]]}
    for part in parts[1:]:
        key, value = part.split("=", 1)
        prof[key] = value if value != "" else None
    return prof


def _pair(before, after, length):
    """"B>A", ">A" (nur nachher) oder "B" (nur vorher)."""
    if not before and not after:
        return None
    if not after:
        return short(before, length)
    return "%s>%s" % (short(before, length) if before else "", short(after, length))


def _split_pair(value):
    before, sep, after = value.partition(">")
    return before or None, (after or None) if sep else None


# -- Schreiben ---------------------------------------------------------------

class _Writer(object):
    def __init__(self, level, hash_len):
        if level not in LEVELS:
            raise PatchInvalid("Pruefstufe muss %s sein" % ", ".join(LEVELS))
        self.level = level
        self.hash_len = hash_len
        self.blocks = []

    def block(self, lines, flag=None):
        n = len(self.blocks) + 1
        if flag == "b64":
            data = lines["base64"]
            lines = [data[i:i + B64_WIDTH] for i in range(0, len(data), B64_WIDTH)]
        head = "@%d %d" % (n, len(lines)) + (" " + flag if flag else "")
        self.blocks.append([head] + list(lines))
        return "@%d" % n

    def pair(self, tokens, key, obj, before, after):
        value = _pair(obj.get(before), obj.get(after), self.hash_len)
        if value:
            tokens.append("%s=%s" % (key, value))

    def entry(self, entry):
        action = entry["action"]
        tokens = [CMD_OF[action], _quote(entry["path"])]
        if action in ("move", "copy"):
            tokens.append(_quote(entry["to"]))
        prof = profile_short(entry.get("compare"))
        if prof:
            tokens.append(prof)
        self.pair(tokens, "h", entry, "hash_before", "hash_after")
        if self.level == "full":
            self.pair(tokens, "t", entry, "text_hash_before", "text_hash_after")
        layout = entry.get("layout") or {}
        if layout.get("indent") == "tabs":
            tokens.append("ind=tab")
        elif layout.get("indent") == "spaces":
            tokens.append("ind=%d" % layout["width"])
        if entry.get("newline"):
            tokens.append("nl=" + entry["newline"])
        if entry.get("final_newline") is False:
            tokens.append("eof=0")
        if entry.get("source"):
            tokens.append("src=" + _quote(entry["source"]))
        elif action in ("add", "replace") and "new" in entry:
            new = entry["new"]
            if isinstance(new, dict):
                tokens.append(self.block(new, "b64"))
            else:
                tokens.append(self.block(new, "lit" if entry.get("literal") else None))
        out = [" ".join(tokens)]
        for ch in entry.get("changes") or []:
            out.append("  " + self.change(ch))
        return out

    def change(self, ch):
        op = ch["op"]
        flag = "lit" if ch.get("literal") else None
        tokens = [CMD_OF[op]]
        q = _quote
        if op in ("replace_unit", "delete_unit"):
            tokens.append(q(ch["unit"]))
        elif op == "add_unit":
            if ch.get("unit"):
                tokens.append(q(ch["unit"]))
            tokens.append(q(ch["position"]))
        elif op == "copy_unit":
            tokens += [q(ch["from"]), q(ch["position"])]
        elif op == "move_unit":
            if "from" in ch:
                tokens += [q(ch["from"]), q(ch["position"])]
            elif "to" in ch:
                tokens += [q(ch["unit"]), "to=" + q(ch["to"])]
            else:
                tokens += [q(ch["unit"]), q(ch["position"])]
            if ch.get("move_id"):
                tokens.append("id=" + q(ch["move_id"]))
        elif op == "insert_text":
            tokens.append(ch["position"])
        if ch.get("as"):
            tokens.append("as=" + q(ch["as"]))
        if ch.get("scope"):
            tokens.append("scope=" + q(ch["scope"]))
        if self.level != "min":
            if ch.get("unit_after") and ch["unit_after"] != ch.get("unit"):
                tokens.append("after=" + q(ch["unit_after"]))
            self.pair(tokens, "u", ch, "unit_hash_before", "unit_hash_after")
            if ch.get("source_unit_hash"):
                tokens.append("s=" + short(ch["source_unit_hash"], self.hash_len))
        if self.level == "full":
            self.pair(tokens, "c", ch, "file_hash_before", "file_hash_after")
        if op in ("replace_text", "delete_text"):
            tokens.append(self.block(ch["old"], flag))
        if op == "insert_text":
            tokens.append(self.block(ch["anchor"], flag))
        if op in ("replace_unit", "add_unit", "replace_text", "insert_text"):
            tokens.append(self.block(ch["new"], flag))
        return " ".join(tokens)


def dumps_body(patch, level="full", hash_len=None, compress=False):
    """Befehlsliste + Datenbloecke (ohne Kopfzeile); compress: Datenbloecke
    gemeinsam komprimieren (zlib + Base64), die Befehlsliste bleibt lesbar."""
    writer = _Writer(level, hash_len)
    lines = ["TITLE " + patch["title"]]
    uses_at = len(lines)
    for line in (patch.get("description") or "").splitlines():
        lines.append("DESC " + line)
    for key, meta in (("author", "AUTHOR"), ("created", "DATE"), ("generator", "GEN")):
        if patch.get(key):
            lines.append("%s %s" % (meta, patch[key]))
    if patch.get("requires"):
        lines.append("REQ " + " ".join(patch["requires"]))
    if patch.get("match", "file") != "file":
        lines.append("MATCH " + patch["match"])
    default = (patch.get("defaults") or {}).get("compare")
    if default:
        lines.append("DEFAULT " + profile_short(default))
    state = _pair(patch.get("state_before"), patch.get("state_after"), hash_len)
    if state:
        lines.append("STATE " + state)
    if patch.get("flow"):
        depth = 0
        for item in patch["flow"]:
            stmt = item["stmt"]
            if stmt in ("elif", "else", "end"):
                depth -= 1
            pad = "  " * depth
            if stmt == "file":
                lines.extend(pad + line for line in writer.entry(patch["files"][item["index"]]))
            else:
                lines.append(pad + flow_line(item))
            if stmt in ("if", "elif", "else"):
                depth += 1
    else:
        for entry in patch["files"]:
            lines.extend(writer.entry(entry))
    data = [line for block in writer.blocks for line in block]
    uses = ["flow"] if patch.get("flow") else []
    if compress and data:
        packed = compress_lines(data)
        if sum(len(l) + 1 for l in packed) < sum(len(l) + 1 for l in data):
            data = packed
            uses.append("z64")
    if uses:
        lines.insert(uses_at, "USES " + " ".join(uses))
    lines.extend(data)
    return "\n".join(lines) + "\n"


def compress_lines(lines):
    """Datenbloecke -> ein Sammelblock "@0 <n> z64" (zlib Stufe 9, Base64)."""
    raw = ("\n".join(lines) + "\n").encode("utf-8")
    text = base64.b64encode(zlib.compress(raw, 9)).decode("ascii")
    rows = [text[i:i + B64_WIDTH] for i in range(0, len(text), B64_WIDTH)]
    return ["@0 %d z64" % len(rows)] + rows


def _expand(content, where):
    try:
        raw = zlib.decompress(base64.b64decode("".join(content), validate=True))
        text = raw.decode("utf-8")
    except (binascii.Error, ValueError, zlib.error, UnicodeDecodeError) as exc:
        raise PatchInvalid("%s: komprimierter Datenblock beschaedigt (%s)" % (where, exc))
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return lines


def flow_line(item):
    """Steuerbefehl als Zeile (Abschnitt 6.8)."""
    stmt = item["stmt"]
    if stmt == "print":
        return "PRINT " + item["text"]
    if stmt == "exit":
        return ("EXIT " + item["text"]).rstrip()
    if stmt == "input":
        tokens = ["INPUT", item["var"]]
        if item.get("prompt"):
            tokens.append(_quote(item["prompt"]))
        if item.get("choices"):
            tokens.append("choices=" + _quote(",".join(item["choices"])))
        if item.get("default") is not None:
            tokens.append("default=" + _quote(item["default"]))
        return " ".join(tokens)
    if stmt in ("if", "elif"):
        return " ".join([stmt.upper()] + [_quote(t) for t in item["cond"]])
    if stmt in ("else", "end"):
        return stmt.upper()
    if stmt in ("label", "goto"):
        return "%s %s" % (stmt.upper(), item["name"])
    if stmt == "set":
        return "SET %s %s" % (item["var"], _quote(item["value"]))
    raise PatchInvalid("unbekannter Steuerbefehl %r" % stmt)


def dumps(patch, level="full", hash_len=None):
    """Einteiliger Patch als Text (Kopfzeile + Inhalt)."""
    body = dumps_body(patch, level, hash_len)
    return "%s %s %s %s\n%s" % (MAGIC, FORMAT_VERSION, patch["id"],
                                short(sha256_text(body), hash_len or DEFAULT_HASH_LEN), body)


def split(patch_id, body, max_size, hash_len=DEFAULT_HASH_LEN, draft=False):
    """Inhalt in Pakete mit hoechstens max_size Zeichen teilen (nur an Zeilengrenzen).
    draft: Entwurf ohne Hashes (Kopfzeilen "#PPATCH 1 <id>" bzw. "<id> i/n")."""
    total = short(sha256_text(body), hash_len)
    single = "%s %s %s" % (MAGIC, FORMAT_VERSION, patch_id) + ("" if draft else " " + total)
    if not max_size:
        return [single + "\n" + body]
    head_len = len("%s %s %s %s 999/999 %s\n" % (MAGIC, FORMAT_VERSION, patch_id, total,
                                                 "0" * hash_len))
    budget = max_size - head_len
    if budget < 80:
        raise PatchInvalid("--max-size %d ist zu klein" % max_size)
    chunks, cur, size = [], [], 0
    for line in body.splitlines(True):
        if len(line) > budget:
            raise PatchInvalid("Zeile mit %d Zeichen passt nicht in ein Paket (--max-size)"
                               % len(line))
        if size + len(line) > budget:
            chunks.append("".join(cur))
            cur, size = [], 0
        cur.append(line)
        size += len(line)
    chunks.append("".join(cur))
    if len(chunks) == 1:
        return [single + "\n" + body]
    n = len(chunks)
    if draft:
        return ["%s %s %s %d/%d\n%s" % (MAGIC, FORMAT_VERSION, patch_id, i, n, chunk)
                for i, chunk in enumerate(chunks, 1)]
    return ["%s %s %s %s %d/%d %s\n%s" % (MAGIC, FORMAT_VERSION, patch_id, total, i, n,
                                          short(sha256_text(chunk), hash_len), chunk)
            for i, chunk in enumerate(chunks, 1)]


def part_paths(path, count):
    if count == 1:
        return [path]
    stem = path[:-len(EXT)] if path.endswith(EXT) else path
    return ["%s.%d%s" % (stem, i, EXT) for i in range(1, count + 1)]


def save(patch, path, level="std", hash_len=DEFAULT_HASH_LEN, max_size=None, compress=False,
         draft=False):
    """Patch schreiben; liefert (Liste der Dateien, Gesamt-Hash).
    hash_len=None behaelt vorhandene Hashes unveraendert (z.B. bei pack)."""
    body = dumps_body(patch, level, hash_len, compress)
    head_len = hash_len or DEFAULT_HASH_LEN
    parts = split(patch["id"], body, max_size, head_len, draft)
    paths = part_paths(path, len(parts))
    for target, text in zip(paths, parts):
        atomic_write(target, text.encode("utf-8"))
    return paths, short(sha256_text(body), head_len)


# -- Lesen -------------------------------------------------------------------

def _read_parts(path):
    """Datei lesen und an Kopfzeilen "#PPATCH" in Teile zerlegen.

    Mehrere Teile duerfen in einer Datei hintereinander stehen (z.B. aus
    mehreren Chat-Nachrichten zusammenkopiert); Code-Zaeune (```) und Text
    vor der ersten Kopfzeile werden ignoriert.
    """
    try:
        with open(path, encoding="utf-8", newline="") as handle:
            text = handle.read()
    except OSError as exc:
        raise PatchInvalid("%s: nicht lesbar (%s)" % (path, exc))
    except UnicodeDecodeError as exc:
        raise PatchInvalid("%s: kein UTF-8 (%s)" % (path, exc))
    text = text.replace("\r\n", "\n").lstrip("\ufeff")
    parts, current = [], None
    for line in text.split("\n"):
        if line.startswith(MAGIC + " "):
            tokens = line.split()
            if len(tokens) < 3:
                raise PatchInvalid("%s: Kopfzeile unvollstaendig: %s" % (path, line))
            if tokens[1] != FORMAT_VERSION:
                raise PatchInvalid("%s: Formatversion %s wird nicht unterstuetzt" % (path, tokens[1]))
            current = {"head": tokens[2:], "lines": [], "path": path}
            parts.append(current)
        elif current is not None:
            current["lines"].append(line)
    if not parts:
        raise PatchInvalid("%s: keine pypatcher-Datei (Zeile '%s 1 ...' erwartet)" % (path, MAGIC))
    for part in parts:
        lines = part.pop("lines")
        while lines and (lines[-1] == "" or lines[-1].strip() == FENCE):
            lines.pop()
        part["body"] = "\n".join(lines) + "\n" if lines else ""
    return parts


def _trim(body, state):
    """Chat-Reste entfernen: ausserhalb von Datenbloecken beendet ein Code-Zaun
    (```) den Teil. state = [modus, rest] wird ueber Teilgrenzen weitergereicht,
    damit Bloecke ueber mehrere Teile laufen duerfen."""
    out = []
    for line in body.split("\n")[:-1] if body.endswith("\n") else body.split("\n"):
        mode = state[0]
        if mode == "count":
            out.append(line)
            state[1] -= 1
            if state[1] <= 0:
                state[0] = "cmd"
            continue
        if mode == "dot":
            out.append(line)
            if line == "@.":
                state[0] = "cmd"
            continue
        if line.strip().startswith(FENCE):
            if out:                     # schliessender Zaun: Rest bis zur naechsten Kopfzeile ist Chat
                break
            continue
        match = BLOCK_RE.match(line)
        if match and match.group(2) == ".":
            state[0] = "dot"
        elif match and match.group(2) and int(match.group(2)) > 0:
            state[0], state[1] = "count", int(match.group(2))
        out.append(line)
    return "\n".join(out) + "\n" if out else ""


def _kind(head):
    """single: <id> [hash] | part: <id> <hash> <i>/<n> <paket-hash> | draftpart: <id> <i>/<n|?>"""
    if len(head) == 4:
        return "part"
    if len(head) == 2 and "/" in head[1]:
        return "draftpart"
    if len(head) <= 2:
        return "single"
    raise PatchInvalid("Kopfzeile nicht lesbar: %s" % " ".join(head))


def _index(value, where):
    index, _, count = value.partition("/")
    try:
        return int(index), (None if count == "?" else int(count))
    except ValueError:
        raise PatchInvalid("%s: Teilangabe %r ungueltig (z.B. 2/5 oder 2/?)" % (where, value))


def _siblings(path, pid):
    """Weitere Dateien mit Teilen desselben Patches im selben Verzeichnis."""
    match = PART_RE.match(path)
    names = []
    if match:
        names = sorted(glob.glob(match.group(1) + ".*" + EXT))
    names += sorted(glob.glob(os.path.join(os.path.dirname(path) or ".", "*" + EXT)))
    out, seen = [], set([os.path.abspath(path)])
    for name in names:
        if os.path.abspath(name) in seen or not os.path.isfile(name):
            continue
        seen.add(os.path.abspath(name))
        try:
            parts = _read_parts(name)
        except PatchInvalid:
            continue
        out.extend(p for p in parts if p["head"][0] == pid)
    return out


def _complete(parts, kind, path):
    pos = 2 if kind == "part" else 1
    indices = [_index(p["head"][pos], path) for p in parts]
    counts = set(n for _, n in indices if n is not None)
    if len(counts) != 1:
        return False
    return set(i for i, _ in indices) >= set(range(1, counts.pop() + 1))


def _join_parts(path, parts, pid, sealed):
    found, count = {}, None
    for part in parts:
        head = part["head"]
        if sealed:
            index, n = _index(head[2], part["path"])
            if not hash_match(head[3], sha256_text(part["body"])):
                raise PatchInvalid("%s: Paket %d beschaedigt (Paket-Hash stimmt nicht)"
                                   % (part["path"], index))
        else:
            index, n = _index(head[1], part["path"])
        if n is not None:
            if count is not None and n != count:
                raise PatchInvalid("Patch %s: widerspruechliche Teilanzahl (%d/%d)" % (pid, n, count))
            count = n
        if index in found and found[index] != part["body"]:
            raise PatchInvalid("Patch %s: Teil %d doppelt mit verschiedenem Inhalt" % (pid, index))
        found[index] = part["body"]
    if count is None:
        raise PatchInvalid("Patch %s: Gesamtzahl der Teile unbekannt - der letzte Teil muss "
                           "z.B. 3/3 angeben" % pid)
    missing = [str(i) for i in range(1, count + 1) if i not in found]
    if missing:
        raise PatchInvalid("Patch %s: Teile fehlen: %s von %d" % (pid, ", ".join(missing), count))
    if sealed:
        return "".join(found[i] for i in range(1, count + 1))
    state = ["cmd", 0]
    return "".join(_trim(found[i], state) for i in range(1, count + 1))


def load(path, verify=True):
    """Patch oder Entwurf laden; Teile/Pakete werden zusammengesetzt
    (aus derselben Datei und aus Dateien im selben Verzeichnis)."""
    parts = _read_parts(path)
    head = parts[0]["head"]
    pid, kind = head[0], _kind(head)
    total = None
    if kind == "single":
        if len(parts) > 1:
            raise PatchInvalid("%s: mehrere Patches in einer Datei" % path)
        total = head[1] if len(head) > 1 else None
        body = parts[0]["body"] if total else _trim(parts[0]["body"], ["cmd", 0])
    else:
        same = [p for p in parts if p["head"][0] == pid and _kind(p["head"]) == kind]
        if len(same) != len(parts):
            raise PatchInvalid("%s: Teile verschiedener Patches in einer Datei" % path)
        if not _complete(same, kind, path):
            same += [p for p in _siblings(path, pid) if _kind(p["head"]) == kind]
        if kind == "part":
            total = head[1]
            same = [p for p in same if p["head"][1] == total]
        body = _join_parts(path, same, pid, kind == "part")
    if verify:
        if total is None:
            raise PatchInvalid("%s: Entwurf ohne Patch-Hash - zuerst versiegeln (seal) "
                               "oder direkt mit 'run' anwenden" % path)
        if not hash_match(total, sha256_text(body)):
            raise PatchInvalid("%s: Patch-Hash stimmt nicht - Datei beschaedigt oder veraendert"
                               % path)
    patch = parse_body(body, path)
    patch["id"] = pid
    if total is not None:
        patch["patch_hash"] = total
    return patch


def _blocks(lines, where):
    blocks, i = {}, 0
    while i < len(lines):
        if lines[i].strip() == FENCE or not lines[i].strip():
            i += 1
            continue
        match = BLOCK_RE.match(lines[i])
        if not match or match.group(2) is None:
            raise PatchInvalid("%s: Datenblock erwartet in Zeile '%s'" % (where, lines[i][:60]))
        n, count, flag = int(match.group(1)), match.group(2), match.group(3)
        if flag is not None and flag not in BLOCK_FLAGS:
            raise PatchInvalid("%s: Kodierung %r von Block @%d ist dieser Version unbekannt - "
                               "neuere pypatcher-Version verwenden" % (where, flag, n))
        i += 1
        if count == ".":
            end = i
            while end < len(lines) and lines[end] != "@.":
                end += 1
            if end == len(lines):
                raise PatchInvalid("%s: Block @%d ohne Ende '@.'" % (where, n))
            content, i = lines[i:end], end + 1
        else:
            content = lines[i:i + int(count)]
            if len(content) != int(count):
                raise PatchInvalid("%s: Block @%d unvollstaendig" % (where, n))
            i += int(count)
        if flag == "z64":
            if n != 0:
                raise PatchInvalid("%s: z64 nur fuer den Sammelblock @0" % where)
            inner = _blocks(_expand(content, where), where)
            for k in inner:
                if k in blocks:
                    raise PatchInvalid("%s: Block @%d doppelt" % (where, k))
            blocks.update(inner)
            blocks["compressed"] = True
            continue
        if n in blocks:
            raise PatchInvalid("%s: Block @%d doppelt" % (where, n))
        blocks[n] = (content, flag)
    return blocks


def _split_tokens(line, where):
    try:
        tokens = shlex.split(line, posix=True)
    except ValueError as exc:
        raise PatchInvalid("%s: Zeile nicht lesbar (%s): %s" % (where, exc, line[:60]))
    keys, pos, refs = {}, [], []
    for token in tokens[1:]:
        key, sep, value = token.partition("=")
        if sep and key in KEYS:
            keys[key] = value
        elif REF_RE.match(token):
            refs.append(int(token[1:]))
        else:
            pos.append(token)
    return tokens[0], pos, keys, refs


class _Reader(object):
    def __init__(self, blocks, where):
        self.blocks = blocks
        self.where = where
        self.used = set()

    def data(self, ref, owner):
        if ref not in self.blocks:
            raise PatchInvalid("%s: Block @%d fehlt" % (self.where, ref))
        self.used.add(ref)
        content, flag = self.blocks[ref]
        if flag == "lit":
            owner["literal"] = True
        if flag == "b64":
            return {"base64": "".join(content)}
        return list(content)

    def entry(self, cmd, pos, keys, refs, line):
        what = "%s: %s" % (self.where, line[:60])
        entry = {"action": FILE_CMDS[cmd]}
        if pos and PROFILE_RE.match(pos[-1]) and len(pos) > 1:
            entry["compare"] = profile_long(pos.pop())
        need = 2 if cmd in ("MOVE", "COPY") else 1
        if len(pos) != need:
            raise PatchInvalid("%s: %d Pfad(e) erwartet" % (what, need))
        entry["path"] = pos[0]
        if need == 2:
            entry["to"] = pos[1]
        self._hashes(entry, keys, "h", "hash_before", "hash_after")
        self._hashes(entry, keys, "t", "text_hash_before", "text_hash_after")
        if "ind" in keys:
            ind = keys["ind"]
            entry["layout"] = {"indent": "tabs"} if ind == "tab" else \
                {"indent": "spaces", "width": self._int(ind, what)}
        if "nl" in keys:
            entry["newline"] = keys["nl"]
        if keys.get("eof") == "0":
            entry["final_newline"] = False
        if "src" in keys:
            entry["source"] = keys["src"]
        if refs:
            if cmd not in ("ADD", "REP") or len(refs) != 1:
                raise PatchInvalid("%s: Datenblock hier nicht erlaubt" % what)
            entry["new"] = self.data(refs[0], entry)
        if cmd == "FILE":
            entry["changes"] = []
        return entry

    def change(self, cmd, pos, keys, refs, line):
        what = "%s: %s" % (self.where, line[:60])
        op = CHANGE_CMDS[cmd]
        ch = {"op": op}
        units = [p for p in pos if not POS_RE.match(p) and p not in ("before", "after")]
        places = [p for p in pos if POS_RE.match(p)]
        if op in ("replace_unit", "delete_unit"):
            self._expect(units, 1, what)
            ch["unit"] = units[0]
        elif op == "add_unit":
            self._expect(places, 1, what)
            ch["position"] = places[0]
            if units:
                ch["unit"] = units[0]
        elif op == "copy_unit":
            self._expect(units, 1, what)
            self._expect(places, 1, what)
            ch["from"], ch["position"] = units[0], places[0]
        elif op == "move_unit":
            self._expect(units, 1, what)
            if "to" in keys:
                ch["unit"], ch["to"] = units[0], keys["to"]
            elif "id" in keys:
                self._expect(places, 1, what)
                ch["from"], ch["position"] = units[0], places[0]
            else:
                self._expect(places, 1, what)
                ch["unit"], ch["position"] = units[0], places[0]
            if "id" in keys:
                ch["move_id"] = keys["id"]
        elif op == "insert_text":
            where = [p for p in pos if p in ("before", "after")]
            self._expect(where, 1, what)
            ch["position"] = where[0]
        for key, name in (("as", "as"), ("scope", "scope"), ("after", "unit_after"),
                          ("s", "source_unit_hash")):
            if key in keys:
                ch[name] = keys[key]
        self._hashes(ch, keys, "u", "unit_hash_before", "unit_hash_after")
        self._hashes(ch, keys, "c", "file_hash_before", "file_hash_after")
        names = {"replace_unit": ("new",), "add_unit": ("new",), "replace_text": ("old", "new"),
                 "insert_text": ("anchor", "new"), "delete_text": ("old",),
                 }.get(op, ())
        if len(refs) != len(names):
            raise PatchInvalid("%s: %d Datenblock/-bloecke erwartet" % (what, len(names)))
        for name, ref in zip(names, refs):
            ch[name] = self.data(ref, ch)
        return ch

    @staticmethod
    def _hashes(obj, keys, key, before, after):
        if key in keys:
            b, a = _split_pair(keys[key])
            if b:
                obj[before] = b
            if a:
                obj[after] = a

    @staticmethod
    def _expect(values, count, what):
        if len(values) != count:
            raise PatchInvalid("%s: Angaben unvollstaendig" % what)

    @staticmethod
    def _int(value, what):
        try:
            return int(value)
        except ValueError:
            raise PatchInvalid("%s: Zahl erwartet: %r" % (what, value))


def parse_body(body, where="Patch"):
    lines = body.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    first = next((i for i, l in enumerate(lines) if BLOCK_RE.match(l)), len(lines))
    for line in lines[:first]:          # USES vor allem anderen pruefen (Abschnitt 6.3)
        word, _, rest = line.strip().partition(" ")
        if word == "USES":
            unknown = [f for f in rest.split() if f not in FEATURES]
            if unknown:
                raise PatchInvalid("%s: der Patch nutzt Funktionen, die diese Version nicht "
                                   "kennt: %s - neuere pypatcher-Version verwenden"
                                   % (where, ", ".join(unknown)))
    blocks = _blocks(lines[first:], where)
    compressed = bool(blocks.pop("compressed", False))
    reader = _Reader(blocks, where)
    patch = {"format": FORMAT, "files": [], "requires": [], "match": "file"}
    if compressed:
        patch["compressed"] = True
    desc, flow, has_flow = [], [], False
    entry = last_file = None
    for raw in lines[:first]:
        line = raw.rstrip()
        if not line.strip() or line.lstrip().startswith(("#", FENCE)):
            continue
        word, _, rest = line.strip().partition(" ")
        if word in FLOW:
            flow.append(_flow_item(word, rest, line.strip(), where))
            has_flow = True
            entry = None
            continue
        if word in META:
            if word == "TITLE":
                patch["title"] = rest
            elif word == "DESC":
                desc.append(rest)
            elif word == "AUTHOR":
                patch["author"] = rest
            elif word == "DATE":
                patch["created"] = rest
            elif word == "GEN":
                patch["generator"] = rest
            elif word == "USES":
                pass
            elif word == "REQ":
                patch["requires"] = rest.split()
            elif word == "MATCH":
                patch["match"] = rest.strip()
            elif word == "DEFAULT":
                patch["defaults"] = {"compare": profile_long(rest.strip())}
            elif word == "STATE":
                b, a = _split_pair(rest.strip())
                patch["state_before"], patch["state_after"] = b, a
            continue
        cmd, pos, keys, refs = _split_tokens(line.strip(), where)
        if cmd in FILE_CMDS:
            entry = last_file = reader.entry(cmd, pos, keys, refs, line.strip())
            flow.append({"stmt": "file", "index": len(patch["files"])})
            patch["files"].append(entry)
        elif cmd in CHANGE_CMDS:
            if entry is None and last_file is not None and last_file["action"] == "modify":
                # Aenderung nach IF/ELSE/... innerhalb eines FILE: eigener Dateibefehl im Zweig
                entry = dict((k, v) for k, v in last_file.items()
                             if k in ("action", "path", "compare", "layout"))
                entry["changes"] = []
                flow.append({"stmt": "file", "index": len(patch["files"])})
                patch["files"].append(entry)
            if entry is None or entry["action"] != "modify":
                raise PatchInvalid("%s: %s nur innerhalb eines FILE-Befehls" % (where, cmd))
            entry["changes"].append(reader.change(cmd, pos, keys, refs, line.strip()))
        else:
            raise PatchInvalid("%s: unbekannter Befehl %r" % (where, cmd))
    unused = sorted(set(reader.blocks) - reader.used)
    if unused:
        raise PatchInvalid("%s: Datenbloecke ohne Befehl: %s" % (where, ", ".join(
            "@%d" % n for n in unused)))
    if desc:
        patch["description"] = "\n".join(desc)
    if has_flow:
        _drop_empty(patch, flow)
        patch["flow"] = flow
    return patch


def _drop_empty(patch, flow):
    """FILE-Befehle ohne eigene Aenderungen (nur Rahmen fuer IF-Zweige) entfallen."""
    keep = [i for i, e in enumerate(patch["files"])
            if not (e["action"] == "modify" and not e["changes"] and "hash_before" not in e)]
    new_index = dict((old, new) for new, old in enumerate(keep))
    patch["files"] = [patch["files"][i] for i in keep]
    flow[:] = [dict(item, index=new_index[item["index"]]) if item["stmt"] == "file" else item
               for item in flow if item["stmt"] != "file" or item["index"] in new_index]


def _flow_item(word, rest, line, where):
    """Steuerbefehl lesen (Abschnitt 6.8)."""
    what = "%s: %s" % (where, line[:60])
    if word == "PRINT":
        return {"stmt": "print", "text": rest}
    if word == "EXIT":
        return {"stmt": "exit", "text": rest.strip()}
    try:
        tokens = shlex.split(rest, posix=True)
    except ValueError as exc:
        raise PatchInvalid("%s: Zeile nicht lesbar (%s)" % (what, exc))
    if word in ("ELSE", "END"):
        if tokens:
            raise PatchInvalid("%s: %s erwartet keine Angaben" % (what, word))
        return {"stmt": word.lower()}
    if word in ("LABEL", "GOTO"):
        if len(tokens) != 1 or not re.match(r"^[A-Za-z_][\w.-]*$", tokens[0]):
            raise PatchInvalid("%s: %s braucht einen Namen" % (what, word))
        return {"stmt": word.lower(), "name": tokens[0]}
    if word == "SET":
        if len(tokens) != 2:
            raise PatchInvalid("%s: SET name wert" % what)
        return {"stmt": "set", "var": tokens[0], "value": tokens[1]}
    if word == "INPUT":
        item = {"stmt": "input", "var": None, "prompt": "", "choices": None, "default": None}
        rest_tokens = []
        for token in tokens:
            key, sep, value = token.partition("=")
            if sep and key == "choices":
                item["choices"] = [c.strip() for c in value.split(",") if c.strip()]
            elif sep and key == "default":
                item["default"] = value
            else:
                rest_tokens.append(token)
        if not rest_tokens or not re.match(r"^[A-Za-z_]\w*$", rest_tokens[0]):
            raise PatchInvalid("%s: INPUT braucht einen Variablennamen" % what)
        item["var"] = rest_tokens[0]
        item["prompt"] = " ".join(rest_tokens[1:])
        if item["choices"] and item["default"] is not None and \
                item["default"] not in item["choices"]:
            raise PatchInvalid("%s: default ist keine der choices" % what)
        return item
    if word in ("IF", "ELIF"):
        if not (len(tokens) == 1 or (len(tokens) == 3 and tokens[1] in ("==", "!=", "in"))):
            raise PatchInvalid("%s: Bedingung z.B. 'IF var == wert', 'IF var != wert', "
                               "'IF var in a,b' oder 'IF var'" % what)
        return {"stmt": word.lower(), "cond": tokens}
    raise PatchInvalid("%s: unbekannter Steuerbefehl" % what)


# -- Pruefung ----------------------------------------------------------------

def validate(patch, sealed=True):
    """Struktur pruefen (Inhalte prueft die Engine)."""
    for key in ("id", "title"):
        if not isinstance(patch.get(key), str) or not patch[key].strip():
            raise PatchInvalid("%s fehlt" % ("Kennung (id)" if key == "id" else "TITLE"))
    if not ID_RE.match(patch["id"]):
        raise PatchInvalid("id %r: erlaubt sind Buchstaben, Ziffern, '.', '_', '-'" % patch["id"])
    if patch.get("match", "file") not in ("file", "state", "unit"):
        raise PatchInvalid("MATCH muss file, state oder unit sein")
    if not patch.get("files"):
        raise PatchInvalid("Patch enthaelt keine Befehle")
    moves = {}
    for n, entry in enumerate(patch["files"], 1):
        path = norm_path(entry.get("path"), "Datei %d: Pfad" % n)
        what = "Datei %d (%s)" % (n, path)
        if entry.get("action") not in ACTIONS:
            raise PatchInvalid("%s: unbekannte Aktion" % what)
        if entry["action"] in ("move", "copy"):
            norm_path(entry.get("to"), what + ": Ziel")
        if entry["action"] in ("add", "replace") and "new" not in entry and "source" not in entry:
            raise PatchInvalid("%s: Inhalt fehlt" % what)
        if entry["action"] == "modify" and not entry.get("changes"):
            raise PatchInvalid("%s: FILE ohne Aenderungen" % what)
        for i, ch in enumerate(entry.get("changes") or [], 1):
            cwhat = "%s, Aenderung %d" % (what, i)
            if ch.get("op") not in OPS:
                raise PatchInvalid("%s: unbekannte Aenderung" % cwhat)
            if ch["op"] == "move_unit" and ("from" in ch or "to" in ch):
                if not ch.get("move_id"):
                    raise PatchInvalid("%s: UMOVE zwischen Dateien braucht id=" % cwhat)
                if "to" in ch:
                    norm_path(ch["to"], cwhat + ": to")
                moves.setdefault(ch["move_id"], []).append("from" if "from" in ch else "to")
    for mid, halves in moves.items():
        if sorted(halves) != ["from", "to"]:
            raise PatchInvalid("UMOVE id=%s: es muessen genau ein Quell- und ein Zielteil "
                               "vorhanden sein" % mid)
