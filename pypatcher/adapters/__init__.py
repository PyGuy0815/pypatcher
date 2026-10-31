"""Adapter-Registry und Hash-Berechnung gemaess Vergleichsprofil."""

from ..hashing import sha256_bytes, sha256_text
from ..profile import profile_id
from .base import ParseError
from .clike import CAdapter, CppAdapter, JavaAdapter
from .css import CssAdapter, ScssAdapter
from .js import JsAdapter, JsxAdapter, TsAdapter, TsxAdapter
from .jsondata import JsonAdapter
from .markup import HtmlAdapter, XmlAdapter
from .python import PythonAdapter
from .text import TextAdapter

ADAPTERS = {"text": TextAdapter(), "python": PythonAdapter(), "js": JsAdapter(),
            "css": CssAdapter(), "scss": ScssAdapter(), "html": HtmlAdapter(),
            "xml": XmlAdapter(), "c": CAdapter(), "cpp": CppAdapter(), "json": JsonAdapter(),
            "ts": TsAdapter(), "tsx": TsxAdapter(), "jsx": JsxAdapter(), "java": JavaAdapter()}
PY = ADAPTERS["python"]

__all__ = ["ADAPTERS", "PY", "ParseError", "get", "file_hash", "unit_hash", "text_hash"]


def get(mode):
    return ADAPTERS[mode]


def _digest(prof, kind, normal, doc):
    head = profile_id(prof) + "\n" + kind + "\n"
    if prof.get("eol") == "keep":
        head += "eol=%r\n" % doc.eol
    return sha256_text(head + normal)


def file_hash(doc, prof):
    """Funktions-Hash einer Datei (Abschnitt 4.2)."""
    if prof["mode"] == "raw":
        return sha256_bytes(doc.raw)
    return _digest(prof, "file", ADAPTERS[prof["mode"]].normalize(doc, prof), doc)


def unit_hash(doc, info, unit, prof):
    return _digest(prof, "unit", ADAPTERS[prof["mode"]].normalize_unit(doc, info, unit, prof), doc)


def text_hash(doc):
    """Text-Hash: exakter Text, nur Zeilenenden/BOM vereinheitlicht."""
    if doc.is_raw:
        return sha256_bytes(doc.raw)
    return sha256_text(doc.text())
