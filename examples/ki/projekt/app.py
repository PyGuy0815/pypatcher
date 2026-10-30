"""Kleine Notiz-App (Beispielprojekt fuer pypatcher)."""

import json

NOTES_FILE = "notes.json"


def load_notes(path=NOTES_FILE):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


class NoteStore:
    """Haelt Notizen im Speicher."""

    def __init__(self):
        self.notes = []

    def add(self, text):
        self.notes.append(text)
        return len(self.notes)

    def find(self, word):
        return [n for n in self.notes if word in n]
