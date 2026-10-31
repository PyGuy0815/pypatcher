"""Farbige Ausgabe (R030): gruen = Erfolg, rot = Fehler/Abbruch,
gelb = Warnung, cyan = Hinweis.

Farben nur am Terminal (auto), nie in Dateien oder Pipes - Ausgaben fuer
Logs oder eine KI bleiben frei von Steuerzeichen. Abschaltbar mit
--color never oder der Umgebungsvariable NO_COLOR (https://no-color.org).
"""

import os
import re
import sys

CODES = {"ok": "1;32", "error": "1;31", "warn": "1;33", "info": "1;36",
         "plus": "32", "minus": "31"}
MODES = ("auto", "always", "never")
MODE = "auto"
_windows_ready = None


def _windows_vt():
    """Windows-Konsole: ANSI-Farben einschalten (einmalig); False, wenn nicht moeglich."""
    global _windows_ready
    if _windows_ready is None:
        _windows_ready = False
        try:
            import ctypes
            kernel = ctypes.windll.kernel32
            handle = kernel.GetStdHandle(-11)
            mode = ctypes.c_uint32()
            if kernel.GetConsoleMode(handle, ctypes.byref(mode)):
                _windows_ready = bool(kernel.SetConsoleMode(handle, mode.value | 0x0004))
        except Exception:
            _windows_ready = False
    return _windows_ready


def enabled(stream):
    if MODE == "always":
        return True
    if MODE == "never" or os.environ.get("NO_COLOR") or os.environ.get("TERM") == "dumb":
        return False
    try:
        if not stream.isatty():
            return False
    except (AttributeError, ValueError):
        return False
    return os.name != "nt" or _windows_vt()


def paint(text, kind, stream=None):
    if not text or not enabled(stream or sys.stdout):
        return text
    return "\033[%sm%s\033[0m" % (CODES[kind], text)


LABEL_RE = re.compile(r"^(INTERNER FEHLER|FEHLER|ABBRUCH|ACHTUNG|Hinweis|OK)\b")
LABEL_KIND = {"INTERNER FEHLER": "error", "FEHLER": "error", "ABBRUCH": "error",
              "ACHTUNG": "warn", "Hinweis": "info", "OK": "ok"}
DIFF_RE = re.compile(r"^(\s+)([-+])(.*)$")


def label(line, stream=None):
    """Kennwort am Zeilenanfang einfaerben (FEHLER, OK, Hinweis ...); in
    Konfliktmeldungen zusaetzlich die Diff-Zeilen (- Patch rot, + Datei gruen)."""
    stream = stream or sys.stdout
    if not enabled(stream):
        return line
    out = []
    for i, part in enumerate(line.split("\n")):
        match = LABEL_RE.match(part)
        if match:
            word = match.group(1)
            part = paint(word, LABEL_KIND[word], stream) + part[len(word):]
        elif i:
            diff = DIFF_RE.match(part)
            if diff and diff.group(1).startswith("      "):
                kind = "minus" if diff.group(2) == "-" else "plus"
                part = diff.group(1) + paint(diff.group(2) + diff.group(3), kind, stream)
        out.append(part)
    return "\n".join(out)
