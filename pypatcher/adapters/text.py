"""mode text - allgemeine Textdateien (manifest.txt Abschnitt 5.4)."""

from .base import Adapter


class TextAdapter(Adapter):
    mode = "text"
    label = "Text"

    def normalize(self, doc, prof):
        lines = doc.lines
        if prof["comments"] == "ignore":
            prefix = prof["comment_prefix"]
            lines = [line for line in lines if not line.lstrip().startswith(prefix)]
        if prof["whitespace"] == "ignore":
            lines = [line.rstrip(" \t") for line in lines]
            while lines and lines[-1] == "":
                lines.pop()
            return "\n".join(lines)
        return "\n".join(lines) + ("\n" if doc.final_nl and lines else "")
