"""Hash-Grundfunktionen (manifest.txt Abschnitt 4)."""

import hashlib

PREFIX = "sha256:"


def sha256_bytes(data):
    return PREFIX + hashlib.sha256(data).hexdigest()


def sha256_text(text):
    return sha256_bytes(text.encode("utf-8"))


def state_hash(hashes):
    """Zustands-Hash ueber {pfad: hash} (Abschnitt 4.4)."""
    return sha256_text("".join("%s\0%s\n" % (p, h) for p, h in sorted(hashes.items())))
