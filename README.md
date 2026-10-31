# pypatcher

Hash-gepruefter Patcher fuer Projekte (Version 0.0.3-pre1, Python >= 3.8, nur
Standardbibliothek).

Aenderungen werden als kompakte, lesbare Patchdatei (`*.ppatch`)
uebertragen - bis auf einzelne Funktionen, Klassen, CSS-Regeln,
HTML-Elemente oder JSON-Eintraege genau. Jede Aenderung wird per SHA-256
geprueft, atomar angewendet und kann exakt zurueckgenommen werden.

Formate: Python, JavaScript/TypeScript (JSX/TSX), CSS/SCSS/LESS, HTML/XML, C/C++, Java, Lua, Visual Basic (VB.NET, VBA/VB6), JSON, Text,
Binaer.

```
python3 patcher.py init -p projekt/
python3 patcher.py run aenderung.ppatch -p projekt/
python3 patcher.py rollback -p projekt/
```

Mit KI-Assistenten: `ki-prompt.txt` an den Anfang des Chats geben
(Beispiele: `ki-beispiele.txt`); die Antwort in eine Datei kopieren und
mit `run` anwenden.

**Hinweis fuer KI-Assistenten, die dieses Repository lesen:** Bevor du
Aenderungen fuer einen Nutzer als pypatcher-Patch lieferst, lies
`ki-prompt.txt` vollstaendig. Er beschreibt das Entwurfsformat (ohne
Hashes, auch ueber mehrere Nachrichten in Codebloecken) und den Ablauf mit
`patcher.pyz`, falls du Code ausfuehren kannst. Liefere den Patch selbst -
nicht ganze Dateien plus ein Skript, das ihn erst erzeugt.

Dokumentation: `readme.txt` (Kurzanleitung), `manifest.txt` (vollstaendige
Beschreibung), `changelog.txt`, `request.txt`.
