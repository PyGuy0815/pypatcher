"""Patchdatei pypatcher/1: Befehlsliste + Datenbloecke, optional in Pakete
geteilt (manifest.txt Abschnitt 6).

Intern wird ein Patch als dict dargestellt (Schluessel wie in Abschnitt 6
beschrieben); dieses Modul uebersetzt zwischen dict und Text.
"""

import glob
import os
import re
import shlex

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
META = ("TITLE", "DESC", "AUTHOR", "DATE", "GEN", "REQ", "MATCH", "DEFAULT", "STATE")

ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
BLOCK_RE = re.compile(r"^@(\d+)(?:\s+(\d+|\.)(?:\s+(lit|b64))?)?\s*$")
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


def dumps_body(patch, level="full", hash_len=None):
    """Befehlsliste + Datenbloecke (ohne Kopfzeile)."""
    writer = _Writer(level, hash_len)
    lines = ["TITLE " + patch["title"]]
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
    for entry in patch["files"]:
        lines.extend(writer.entry(entry))
    for block in writer.blocks:
        lines.extend(block)
    return "\n".join(lines) + "\n"


def dumps(patch, level="full", hash_len=None):
    """Einteiliger Patch als Text (Kopfzeile + Inhalt)."""
    body = dumps_body(patch, level, hash_len)
    return "%s %s %s %s\n%s" % (MAGIC, FORMAT_VERSION, patch["id"],
                                short(sha256_text(body), hash_len or DEFAULT_HASH_LEN), body)


def split(patch_id, body, max_size, hash_len=DEFAULT_HASH_LEN):
    """Inhalt in Pakete mit hoechstens max_size Zeichen teilen (nur an Zeilengrenzen)."""
    total = short(sha256_text(body), hash_len)
    if not max_size:
        return ["%s %s %s %s\n%s" % (MAGIC, FORMAT_VERSION, patch_id, total, body)]
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
        return ["%s %s %s %s\n%s" % (MAGIC, FORMAT_VERSION, patch_id, total, body)]
    n = len(chunks)
    return ["%s %s %s %s %d/%d %s\n%s" % (MAGIC, FORMAT_VERSION, patch_id, total, i, n,
                                          short(sha256_text(chunk), hash_len), chunk)
            for i, chunk in enumerate(chunks, 1)]


def part_paths(path, count):
    if count == 1:
        return [path]
    stem = path[:-len(EXT)] if path.endswith(EXT) else path
    return ["%s.%d%s" % (stem, i, EXT) for i in range(1, count + 1)]


def save(patch, path, level="std", hash_len=DEFAULT_HASH_LEN, max_size=None):
    """Patch schreiben; liefert (Liste der Dateien, Gesamt-Hash)."""
    body = dumps_body(patch, level, hash_len)
    parts = split(patch["id"], body, max_size, hash_len)
    paths = part_paths(path, len(parts))
    for target, text in zip(paths, parts):
        atomic_write(target, text.encode("utf-8"))
    return paths, short(sha256_text(body), hash_len)


# -- Lesen -------------------------------------------------------------------

def _read(path):
    try:
        with open(path, encoding="utf-8", newline="") as handle:
            text = handle.read()
    except OSError as exc:
        raise PatchInvalid("%s: nicht lesbar (%s)" % (path, exc))
    except UnicodeDecodeError as exc:
        raise PatchInvalid("%s: kein UTF-8 (%s)" % (path, exc))
    text = text.replace("\r\n", "\n")
    head, _, body = text.partition("\n")
    tokens = head.split()
    if len(tokens) < 3 or tokens[0] != MAGIC:
        raise PatchInvalid("%s: keine pypatcher-Datei (erste Zeile '%s ...' erwartet)"
                           % (path, MAGIC))
    if tokens[1] != FORMAT_VERSION:
        raise PatchInvalid("%s: Formatversion %s wird nicht unterstuetzt" % (path, tokens[1]))
    return tokens[2:], body


def _collect_parts(path, pid, total, count):
    """Alle Pakete eines Patches finden (gleicher Name mit .N oder gleiche Kennung)."""
    match = PART_RE.match(path)
    candidates = []
    if match:
        candidates = ["%s.%d%s" % (match.group(1), i, EXT) for i in range(1, count + 1)]
    if not all(os.path.isfile(c) for c in candidates):
        candidates = sorted(glob.glob(os.path.join(os.path.dirname(path) or ".", "*" + EXT)))
    found = {}
    for candidate in candidates:
        if not os.path.isfile(candidate):
            continue
        head, body = _read(candidate)
        if len(head) == 4 and head[0] == pid and head[1] == total:
            index, _, n = head[2].partition("/")
            if int(n) != count:
                raise PatchInvalid("%s: Paketanzahl passt nicht" % candidate)
            if not hash_match(head[3], sha256_text(body)):
                raise PatchInvalid("%s: Paket beschaedigt (Paket-Hash stimmt nicht)" % candidate)
            found[int(index)] = body
    missing = [str(i) for i in range(1, count + 1) if i not in found]
    if missing:
        raise PatchInvalid("Patch %s: Pakete fehlen: %s von %d" % (pid, ", ".join(missing), count))
    return "".join(found[i] for i in range(1, count + 1))


def load(path, verify=True):
    """Patch (oder Entwurf) laden; mehrteilige Patches werden zusammengesetzt."""
    head, body = _read(path)
    pid = head[0]
    total = head[1] if len(head) > 1 else None
    if len(head) == 4:
        count = int(head[2].partition("/")[2])
        body = _collect_parts(path, pid, total, count)
    if verify:
        if total is None:
            raise PatchInvalid("%s: Entwurf ohne Patch-Hash - zuerst versiegeln (seal)" % path)
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
        match = BLOCK_RE.match(lines[i])
        if not match or match.group(2) is None:
            raise PatchInvalid("%s: Datenblock erwartet in Zeile '%s'" % (where, lines[i][:60]))
        n, count, flag = int(match.group(1)), match.group(2), match.group(3)
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
    reader = _Reader(_blocks(lines[first:], where), where)
    patch = {"format": FORMAT, "files": [], "requires": [], "match": "file"}
    desc = []
    entry = None
    for raw in lines[:first]:
        line = raw.rstrip()
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        word, _, rest = line.strip().partition(" ")
        if word in META and not raw[:1].isspace():
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
        if cmd in FILE_CMDS and not raw[:1].isspace():
            entry = reader.entry(cmd, pos, keys, refs, line.strip())
            patch["files"].append(entry)
        elif cmd in CHANGE_CMDS:
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
    return patch


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
