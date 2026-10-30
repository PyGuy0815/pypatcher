PYPATCHER - README
==================

Version 0.0.1-pre1 (Vorabversion, noch nicht freigegeben).
Die ausfuehrliche Beschreibung steht in manifest.txt.

Was macht der Patcher?
----------------------
Er uebertraegt Aenderungen an einem Projekt mit einer einzigen Patchdatei
(*.ppatch.json) in eine andere Umgebung - ohne ganze Dateien zu kopieren.
Aenderungen koennen bis auf einzelne Funktionen/Methoden genau beschrieben
werden ("ersetze in Datei X die Funktion Y"). Funktionen/Klassen koennen
ersetzt, eingefuegt, geloescht, kopiert und verschoben werden; innerhalb
einer Funktion lassen sich auch einzelne Zeilen aendern.

Jede Datei und jede einzelne Aenderung wird per SHA-256-Hash geprueft
(vorher und nachher). Geprueft wird die Funktion, nicht die Schreibweise:
Kommentare und unwichtige Leerzeichen/Tabs/Umbrueche zaehlen je nach Format
nicht. Wie genau verglichen wird (raw = binaer gleich, text, python ...),
legt die Patchdatei je Datei fest. Bei Python zaehlt die Einrueckung
(Tiefe, Tabs/Leerzeichen) immer exakt.

Im Patch steht die Einrueckung als Tabs je Ebene relativ zur Zielstelle;
die echte Einrueckung ergibt sich aus der Zieldatei. Jeder angewendete
Patch laesst sich wieder exakt zuruecknehmen. Windows-Zeilenenden (CRLF)
werden erkannt und beibehalten.

Voraussetzungen
---------------
Python 3.8 oder neuer, keine weiteren Pakete. Getestet mit 3.8, 3.11,
3.12 und 3.13. Aufruf: python3 patcher.py BEFEHL ... (das Verzeichnis
pypatcher/ muss neben patcher.py liegen).

Typischer Ablauf
----------------
1. Entwurf schreiben (JSON ohne Hashes), z.B.:

   {
     "id": "0002-fix",
     "title": "add rechnet mit Kommazahlen",
     "files": [
       {"path": "src/rechner.py", "action": "modify", "changes": [
         {"op": "replace_unit", "unit": "add",
          "new": ["def add(a, b):", "\treturn float(a) + float(b)"]}
       ]}
     ]
   }

   "\t" am Zeilenanfang = eine Einrueckungs-Ebene tiefer als die Funktion
   selbst. Weitere Beispiele: examples/entwurf-beispiel.json,
   alle Befehle: manifest.txt Abschnitt 7.

2. Entwurf gegen den Ausgangsstand versiegeln (traegt Hashes ein):
   python3 patcher.py seal entwurf.json --base projekt/ -o 0002-fix.ppatch.json
   python3 patcher.py show 0002-fix.ppatch.json

3. Zielumgebung:
   python3 patcher.py init     -p projekt/          (einmalig)
   python3 patcher.py check    0002-fix.ppatch.json -p projekt/   (Probelauf)
   python3 patcher.py apply    0002-fix.ppatch.json -p projekt/
   python3 patcher.py status   -p projekt/
   python3 patcher.py verify   -p projekt/          (Ist-Stand pruefen)
   python3 patcher.py rollback -p projekt/          (letzten Patch zuruecknehmen)

Weitere Befehle: log, units, recover - siehe "python3 patcher.py -h" und
manifest.txt Abschnitt 13. Spaeter: create, doctor, canon, invert.

Exit-Codes: 0 OK, 1 Bedienfehler, 2 Patch ungueltig, 3 Konflikt,
4 Journalfehler, 5 interner Fehler.

Beispiel ausprobieren
---------------------
   cp -r examples/demo /tmp/ziel
   python3 patcher.py seal examples/entwurf-beispiel.json --base examples/demo -o /tmp/p.json
   python3 patcher.py init -p /tmp/ziel
   python3 patcher.py apply /tmp/p.json -p /tmp/ziel
   python3 patcher.py rollback -p /tmp/ziel

Tests
-----
   cd tests && python3 -m unittest

Dokumentation
-------------
  readme.txt     diese Kurzanleitung
  manifest.txt   ausfuehrliche Programmbeschreibung
  changelog.txt  Aenderungshistorie
  request.txt    Wuensche fuer kuenftige Aenderungen
