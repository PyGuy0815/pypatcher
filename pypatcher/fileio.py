"""Dateien lesen/schreiben mit Erhalt von Zeilenenden, BOM und Encoding."""

import os
import tempfile

from .errors import Conflict

BOM = b"\xef\xbb\xbf"
EOL_NAMES = {"\n": "lf", "\r\n": "crlf", "\r": "cr"}
EOL_BY_NAME = {"lf": "\n", "crlf": "\r\n", "cr": "\r"}


class Doc(object):
    """Dateiinhalt im Speicher.

    Textdateien: lines (ohne Zeilenende) + final_nl; eol/bom/encoding
    beschreiben die Datei auf der Platte. Binaer (mode raw): raw = Bytes.
    """

    def __init__(self, lines=None, final_nl=True, eol="\n", bom=False,
                 encoding="utf-8", raw=None, st_mode=None):
        self.lines = lines
        self.final_nl = final_nl
        self.eol = eol
        self.bom = bom
        self.encoding = encoding
        self.raw = raw
        self.st_mode = st_mode

    @property
    def is_raw(self):
        return self.raw is not None

    def text(self):
        if not self.lines:
            return ""
        return "\n".join(self.lines) + ("\n" if self.final_nl else "")

    def with_lines(self, lines):
        return Doc(list(lines), self.final_nl, self.eol, self.bom,
                   self.encoding, None, self.st_mode)

    def copy(self):
        if self.is_raw:
            return Doc(raw=self.raw, st_mode=self.st_mode)
        return self.with_lines(self.lines)


def decode(data, encoding, where, allow_mixed=False):
    if b"\0" in data:
        raise Conflict("%s: enthaelt NUL-Bytes (Binaerdatei) - mode raw verwenden" % where)
    bom = False
    if encoding.lower().replace("_", "-") in ("utf-8", "utf8") and data.startswith(BOM):
        bom = True
        data = data[len(BOM):]
    try:
        text = data.decode(encoding)
    except (UnicodeDecodeError, LookupError) as exc:
        raise Conflict("%s: nicht als %s lesbar (%s)" % (where, encoding, exc))
    crlf = text.count("\r\n")
    cr = text.count("\r") - crlf
    lf = text.count("\n") - crlf
    if sum(1 for n in (crlf, cr, lf) if n) > 1 and not allow_mixed:
        raise Conflict("%s: gemischte Zeilenenden (Option --allow-mixed-eol)" % where)
    eol = max((lf, "\n"), (crlf, "\r\n"), (cr, "\r"))[1] if (lf or crlf or cr) else "\n"
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if not text:
        return Doc([], False, eol, bom, encoding)
    final_nl = text.endswith("\n")
    if final_nl:
        text = text[:-1]
    return Doc(text.split("\n"), final_nl, eol, bom, encoding)


def encode(doc):
    if doc.is_raw:
        return doc.raw
    text = doc.eol.join(doc.lines) + (doc.eol if doc.final_nl and doc.lines else "")
    data = text.encode(doc.encoding)
    return BOM + data if doc.bom else data


def read_bytes(path):
    with open(path, "rb") as handle:
        return handle.read()


def load_bytes(data, where, prof, allow_mixed=True):
    if prof["mode"] == "raw":
        return Doc(raw=data)
    return decode(data, prof["encoding"], where, allow_mixed)


def load(path, where, prof, allow_mixed=False):
    data = read_bytes(path)
    st_mode = os.stat(path).st_mode
    if prof["mode"] == "raw":
        return Doc(raw=data, st_mode=st_mode)
    doc = decode(data, prof["encoding"], where, allow_mixed)
    doc.st_mode = st_mode
    return doc


def drop_bytecode(path):
    """Zwischengespeicherten Python-Bytecode einer Quelldatei entfernen.

    Python erkennt veralteten Bytecode nur an Aenderungszeit (Sekunden) und
    Groesse der Quelle - wird eine .py-Datei innerhalb einer Sekunde mit
    gleich langem Inhalt ersetzt (Patch, Rollback), liefe sonst alter Code.
    """
    if not path.endswith((".py", ".pyw")):
        return
    directory, name = os.path.split(path)
    stem = os.path.splitext(name)[0]
    cache = os.path.join(directory, "__pycache__")
    if os.path.isdir(cache):
        for entry in os.listdir(cache):
            if entry.startswith(stem + ".") and entry.endswith((".pyc", ".pyo")):
                try:
                    os.remove(os.path.join(cache, entry))
                except OSError:
                    pass


def atomic_write(path, data, st_mode=None):
    """Temp-Datei im selben Verzeichnis, dann os.replace (atomar je Datei).

    Ohne st_mode erhaelt eine neue Datei die ueblichen Rechte (0666 minus
    umask) statt der restriktiven Rechte der Temp-Datei.
    """
    if st_mode is None:
        mask = os.umask(0)
        os.umask(mask)
        st_mode = 0o666 & ~mask
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".pptmp-", dir=directory)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        if st_mode is not None:
            os.chmod(tmp, st_mode & 0o7777)
        os.replace(tmp, path)
        drop_bytecode(path)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise
