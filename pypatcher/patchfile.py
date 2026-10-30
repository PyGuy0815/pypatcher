"""Patchdatei lesen, pruefen, schreiben (manifest.txt Abschnitt 6, 4.7)."""

import json
import re

from . import FORMAT
from .errors import PatchInvalid
from .fileio import atomic_write
from .hashing import sha256_bytes

ACTIONS = ("add", "delete", "modify", "replace", "move", "copy")
OPS = ("replace_unit", "add_unit", "delete_unit", "copy_unit", "move_unit",
       "replace_text", "insert_text", "delete_text")
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

HEAD_ORDER = ("format", "generator", "id", "title", "description", "author", "created",
              "requires", "match", "defaults", "state_before", "state_after", "files",
              "signature", "patch_hash")
ENTRY_ORDER = ("path", "action", "to", "compare", "layout", "literal", "old_literal", "newline",
               "final_newline", "hash_before", "hash_after", "text_hash_before",
               "text_hash_after", "old", "new", "changes")
CHANGE_ORDER = ("op", "unit", "unit_after", "scope", "from", "as", "to", "move_id",
                "position", "literal", "file_hash_before", "file_hash_after",
                "unit_hash_before", "unit_hash_after", "source_unit_hash", "anchor",
                "old", "new")


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


def compute_hash(patch):
    body = dict((k, v) for k, v in patch.items() if k not in ("patch_hash", "signature"))
    data = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return sha256_bytes(data.encode("utf-8"))


def load(path, verify=True):
    try:
        with open(path, encoding="utf-8") as handle:
            patch = json.load(handle)
    except OSError as exc:
        raise PatchInvalid("%s: nicht lesbar (%s)" % (path, exc))
    except ValueError as exc:
        raise PatchInvalid("%s: kein gueltiges JSON (%s)" % (path, exc))
    if not isinstance(patch, dict):
        raise PatchInvalid("%s: Patch muss ein JSON-Objekt sein" % path)
    if verify:
        if patch.get("patch_hash") != compute_hash(patch):
            raise PatchInvalid("%s: patch_hash stimmt nicht - Datei beschaedigt oder veraendert" % path)
        if patch.get("signature") is not None:
            raise PatchInvalid("%s: Signaturen werden in dieser Version noch nicht unterstuetzt" % path)
    return patch


def _ordered(obj, order):
    out = dict((k, obj[k]) for k in order if k in obj)
    out.update((k, v) for k, v in obj.items() if k not in out)
    return out


def ordered(patch):
    patch = _ordered(patch, HEAD_ORDER)
    files = []
    for entry in patch.get("files", []):
        entry = _ordered(entry, ENTRY_ORDER)
        if "changes" in entry:
            entry["changes"] = [_ordered(ch, CHANGE_ORDER) for ch in entry["changes"]]
        files.append(entry)
    patch["files"] = files
    return patch


def dumps(patch):
    return json.dumps(patch, indent=2, ensure_ascii=False) + "\n"


def save(patch, path):
    patch = ordered(patch)
    patch["patch_hash"] = compute_hash(patch)
    atomic_write(path, dumps(patch).encode("utf-8"))
    return patch


def _lines(value, what):
    if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
        raise PatchInvalid("%s muss eine Liste von Zeilen sein" % what)
    if any("\n" in x or "\r" in x for x in value):
        raise PatchInvalid("%s: Zeilen duerfen keine Zeilenumbrueche enthalten" % what)


def validate(patch, sealed=True):
    """Struktur pruefen. sealed=False: Entwurf (Hashes/Altinhalte fehlen)."""
    fmt = patch.get("format")
    if fmt != FORMAT and (sealed or fmt is not None):
        raise PatchInvalid("format muss %r sein (gefunden %r)" % (FORMAT, fmt))
    for key in ("id", "title"):
        if not isinstance(patch.get(key), str) or not patch[key].strip():
            raise PatchInvalid("Feld %r fehlt" % key)
    if not ID_RE.match(patch["id"]):
        raise PatchInvalid("id %r: erlaubt sind Buchstaben, Ziffern, '.', '_', '-'" % patch["id"])
    requires = patch.get("requires", [])
    if not isinstance(requires, list) or not all(isinstance(r, str) for r in requires):
        raise PatchInvalid("requires muss eine Liste von Patch-IDs sein")
    if patch.get("match", "file") not in ("file", "state", "unit"):
        raise PatchInvalid("match muss file, state oder unit sein")
    files = patch.get("files")
    if not isinstance(files, list) or not files:
        raise PatchInvalid("files muss eine nicht-leere Liste sein")
    moves = {}
    for n, entry in enumerate(files, 1):
        what = "Datei %d" % n
        if not isinstance(entry, dict):
            raise PatchInvalid("%s: Eintrag muss ein Objekt sein" % what)
        path = norm_path(entry.get("path"), what + ": path")
        what = "Datei %d (%s)" % (n, path)
        action = entry.get("action")
        if action not in ACTIONS:
            raise PatchInvalid("%s: action muss eine von %s sein" % (what, ", ".join(ACTIONS)))
        if action in ("move", "copy"):
            norm_path(entry.get("to"), what + ": to")
        for key in ("old", "new"):
            if isinstance(entry.get(key), list):
                _lines(entry[key], "%s: %s" % (what, key))
        if action != "modify":
            continue
        changes = entry.get("changes")
        if not isinstance(changes, list) or not changes:
            raise PatchInvalid("%s: changes muss eine nicht-leere Liste sein" % what)
        for i, ch in enumerate(changes, 1):
            cwhat = "%s, Aenderung %d" % (what, i)
            if not isinstance(ch, dict) or ch.get("op") not in OPS:
                raise PatchInvalid("%s: op muss eine von %s sein" % (cwhat, ", ".join(OPS)))
            for key in ("old", "new", "anchor"):
                if key in ch and ch[key] is not None:
                    _lines(ch[key], "%s: %s" % (cwhat, key))
            if ch["op"] == "move_unit" and ("from" in ch or "to" in ch):
                mid = ch.get("move_id")
                if not isinstance(mid, str) or not mid:
                    raise PatchInvalid("%s: move_unit zwischen Dateien braucht move_id" % cwhat)
                half = "from" if "from" in ch else "to"
                if half == "to":
                    norm_path(ch["to"], cwhat + ": to")
                moves.setdefault(mid, []).append(half)
    for mid, halves in moves.items():
        if sorted(halves) != ["from", "to"]:
            raise PatchInvalid("move_id %r: es muessen genau ein 'from'- und ein 'to'-Teil "
                               "vorhanden sein" % mid)
