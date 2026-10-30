PYPATCHER - README
==================

Version 0.0.1-pre1 (Vorabversion, Testphase).
Die ausfuehrliche Beschreibung steht in manifest.txt.

Was macht der Patcher?
----------------------
Er uebertraegt Aenderungen an einem Projekt mit einer kompakten
Patchdatei (*.ppatch) in eine andere Umgebung - ohne ganze Dateien zu
kopieren. Die Patchdatei ist eine Befehlsliste, die von oben nach unten
abgearbeitet wird, gefolgt von den Daten (Code/Text unveraendert). Bei
begrenzter Uebertragungsgroesse wird sie auf Wunsch in Pakete geteilt.

Aenderungen koennen bis auf einzelne Funktionen/Methoden genau
beschrieben werden ("ersetze in Datei X die Funktion Y"). Funktionen/
Klassen koennen ersetzt, eingefuegt, geloescht, kopiert und verschoben
werden; innerhalb einer Funktion lassen sich auch einzelne Zeilen aendern.

Geprueft wird per SHA-256 - so viel wie gewuenscht: nur die Datei (min),
zusaetzlich jede betroffene Funktion (std, Standard) oder alles (full).
Geprueft wird die Funktion, nicht die Schreibweise: Kommentare und
unwichtige Leerzeichen/Tabs/Umbrueche zaehlen je nach Format nicht; bei
Python zaehlt die Einrueckung immer exakt. Formate: Python und
JavaScript (Funktionen/Klassen als Einheiten), CSS/SCSS/LESS (Regeln
als Einheiten), jede Textdatei ueber Textbefehle; HTML/XML folgen. Jeder angewendete Patch laesst
sich exakt zuruecknehmen (Sicherung im Ziel). Windows-Zeilenenden (CRLF)
bleiben erhalten.

Voraussetzungen
---------------
Python 3.8 oder neuer, keine weiteren Pakete. Getestet mit 3.8, 3.11,
3.12 und 3.13. Aufruf: python3 patcher.py BEFEHL ... (das Verzeichnis
pypatcher/ muss neben patcher.py liegen).

So sieht ein Patch aus
----------------------
   #PPATCH 1 0002-fix 9f2c41d07ab3e118
   TITLE add rechnet mit Kommazahlen
   FILE src/rechner.py py h=aa11f0c2d9e4b7a1>ff9903e1c5a7b2d4 ind=4
     UNIT add u=cc33a1b2c3d4e5f6>dd4401a2b3c4d5e6 @1
   @1 2
   def add(a, b):
   	return float(a) + float(b)

Ein Tab am Zeilenanfang = eine Einrueckungs-Ebene tiefer als die Funktion
selbst; die echte Einrueckung ergibt sich aus der Zieldatei.
Alle Befehle: manifest.txt Abschnitt 6.4.

Typischer Ablauf
----------------
1. Patch erzeugen - automatisch aus zwei Staenden:
   python3 patcher.py create alt/ neu/ -o 0002-fix.ppatch \
       --id 0002-fix --title "add rechnet mit Kommazahlen"
   oder einen Entwurf von Hand schreiben (wie oben, ohne Hashes, Bloecke
   mit "@1 ." bis "@.") und versiegeln:
   python3 patcher.py seal entwurf.ppatch --base projekt/ -o 0002-fix.ppatch

   Optionen fuer beide: --verify min|std|full, --hash-len 16,
   --max-size 8000 (Pakete 0002-fix.1.ppatch, .2.ppatch ...).

2. Anzeigen:
   python3 patcher.py show 0002-fix.ppatch

3. Zielumgebung:
   python3 patcher.py init     -p projekt/          (einmalig)
   python3 patcher.py check    0002-fix.ppatch -p projekt/   (Probelauf)
   python3 patcher.py apply    0002-fix.ppatch -p projekt/
   python3 patcher.py status   -p projekt/
   python3 patcher.py verify   -p projekt/          (Ist-Stand pruefen)
   python3 patcher.py rollback -p projekt/          (letzten Patch zuruecknehmen)
   Bei Paketen genuegt ein beliebiges Paket als Angabe; die uebrigen
   muessen im selben Verzeichnis liegen.

Weitere Befehle: log, units, recover - siehe "python3 patcher.py -h" und
manifest.txt Abschnitt 13. Spaeter: doctor, canon, invert.

Exit-Codes: 0 OK, 1 Bedienfehler, 2 Patch ungueltig, 3 Konflikt,
4 Journalfehler, 5 interner Fehler.

Beispiel ausprobieren
---------------------
   cp -r examples/demo /tmp/ziel
   python3 patcher.py seal examples/entwurf-beispiel.ppatch --base examples/demo -o /tmp/p.ppatch
   python3 patcher.py init -p /tmp/ziel
   python3 patcher.py apply /tmp/p.ppatch -p /tmp/ziel
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
