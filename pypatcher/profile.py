"""Vergleichsprofile (manifest.txt Abschnitt 5.2)."""

import fnmatch

from .errors import PatchInvalid, UsageError

# mode -> (Normalform-Version, Optionen). Erster erlaubter Wert = Vorgabe,
# None = freier Wert mit Vorgabe aus FREE_DEFAULTS.
MODES = {
    "raw": (1, {}),
    "text": (1, {
        "comments": ("keep", "ignore"),
        "whitespace": ("ignore", "keep"),
        "eol": ("normalize", "keep"),
        "encoding": None,
        "comment_prefix": None,
    }),
    "python": (1, {
        "comments": ("ignore", "keep"),
        "whitespace": ("ignore", "keep"),
        "docstrings": ("keep", "ignore"),
        "indent": ("exact",),
        "eol": ("normalize", "keep"),
        "encoding": None,
    }),
    "js": (1, {
        "comments": ("ignore", "keep"),
        "whitespace": ("ignore", "keep"),
        "eol": ("normalize", "keep"),
        "encoding": None,
    }),
    "css": (1, {
        "comments": ("ignore", "keep"),
        "whitespace": ("ignore", "keep"),
        "eol": ("normalize", "keep"),
        "encoding": None,
    }),
    "scss": (1, {
        "comments": ("ignore", "keep"),
        "whitespace": ("ignore", "keep"),
        "eol": ("normalize", "keep"),
        "encoding": None,
    }),
    "html": (1, {
        "comments": ("ignore", "keep"),
        "whitespace": ("keep", "ignore"),
        "eol": ("normalize", "keep"),
        "encoding": None,
    }),
    "xml": (1, {
        "comments": ("ignore", "keep"),
        "whitespace": ("keep", "ignore"),
        "eol": ("normalize", "keep"),
        "encoding": None,
    }),
}
FREE_DEFAULTS = {"encoding": "utf-8", "comment_prefix": None}
EXTENSIONS = {
    "python": (".py", ".pyw"),
    "js": (".js", ".mjs", ".cjs"),
    "css": (".css",),
    "scss": (".scss", ".less"),
    "html": (".html", ".htm", ".xhtml"),
    "xml": (".xml", ".svg", ".xsd", ".xsl", ".xslt"),
}


def default_mode(path):
    lower = path.lower()
    for mode, extensions in EXTENSIONS.items():
        if lower.endswith(extensions):
            return mode
    return "text"


def complete(prof, where=""):
    """Prueft ein Profil und ergaenzt alle Optionen mit Vorgaben."""
    if not isinstance(prof, dict):
        raise PatchInvalid("%scompare muss ein Objekt sein" % where)
    mode = prof.get("mode")
    if mode not in MODES:
        raise PatchInvalid("%sunbekannter mode %r" % (where, mode))
    options = MODES[mode][1]
    for key, value in prof.items():
        if key == "mode":
            continue
        if key not in options:
            raise PatchInvalid("%sOption %r ist bei mode %s nicht erlaubt" % (where, key, mode))
        allowed = options[key]
        if allowed is not None and value not in allowed:
            raise PatchInvalid("%s%s=%r ist bei mode %s nicht erlaubt (erlaubt: %s)"
                               % (where, key, value, mode, ", ".join(allowed)))
        if allowed is None and value is not None and not isinstance(value, str):
            raise PatchInvalid("%s%s muss Text sein" % (where, key))
    out = {"mode": mode}
    for key, allowed in options.items():
        if key in prof:
            out[key] = prof[key]
        else:
            out[key] = allowed[0] if allowed is not None else FREE_DEFAULTS[key]
    if mode == "text" and out["comments"] == "ignore" and not out["comment_prefix"]:
        raise PatchInvalid("%scomments=ignore benoetigt comment_prefix" % where)
    return out


def resolve(path, entry=None, patch_default=None, project_default=None):
    """Rangfolge: Dateieintrag > Patch-Vorgabe > Projekt-Vorgabe > Dateiendung.

    Eine Ebene mit anderem mode ersetzt die darunterliegenden vollstaendig.
    """
    prof = {"mode": default_mode(path)}
    for layer in (project_default, patch_default, entry):
        if not layer:
            continue
        if not isinstance(layer, dict):
            raise PatchInvalid("%s: compare muss ein Objekt sein" % path)
        if "mode" in layer and layer["mode"] != prof["mode"]:
            prof = dict(layer)
        else:
            prof.update(layer)
    return complete(prof, path + ": ")


def has_mode(*layers):
    return any(layer and layer.get("mode") for layer in layers)


def profile_id(prof):
    """Eindeutige Kennung, erster Teil jeder Hash-Eingabe."""
    version = MODES[prof["mode"]][0]
    opts = ";".join("%s=%s" % (k, "" if v is None else v)
                    for k, v in sorted(prof.items()) if k != "mode")
    return "%s/%d%s" % (prof["mode"], version, ";" + opts if opts else "")


def parse_short(text):
    """Kurzform wie "python" oder "text,whitespace=keep"."""
    prof = {}
    parts = [p.strip() for p in text.split(",") if p.strip()]
    if not parts:
        raise UsageError("leere Profilangabe")
    for i, part in enumerate(parts):
        if "=" in part:
            key, value = part.split("=", 1)
            prof[key.strip()] = value.strip()
        elif i == 0:
            prof["mode"] = part
        else:
            raise UsageError("ungueltige Profilangabe: %r" % part)
    return prof


def lookup(patterns, path):
    """Erstes passende Muster aus {muster: kurzform oder objekt}."""
    for pattern, value in (patterns or {}).items():
        if fnmatch.fnmatch(path, pattern):
            return parse_short(value) if isinstance(value, str) else dict(value)
    return None


def check_required(prof, path, required):
    """Mindestprofile aus .patcher/config.json ("require")."""
    req = lookup(required, path)
    for key, value in (req or {}).items():
        if prof.get(key) != value:
            raise PatchInvalid("%s: Mindestprofil verlangt %s=%s, Patch hat %s"
                               % (path, key, value, prof.get(key)))
