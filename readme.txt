PYPATCHER - README
==================

Status: KONZEPTPHASE (noch keine Version) - das Programm ist noch nicht
implementiert. Erste Version wird 0.0.1-pre1. Die ausfuehrliche
Beschreibung steht in manifest.txt.

Was macht der Patcher?
----------------------
Er uebertraegt Aenderungen an einem Projekt mit einer einzigen Patchdatei
(*.ppatch.json) in eine andere Umgebung - ohne ganze Dateien zu kopieren.
Aenderungen koennen bis auf einzelne Funktionen/Methoden genau beschrieben
werden ("ersetze in Datei X die Funktion Y"). Jede Datei und jede einzelne
Aenderung wird per SHA-256-Hash geprueft (vorher und nachher). Geprueft
wird die Funktion, nicht die Schreibweise: Kommentare und unwichtige
Leerzeichen/Tabs/Umbrueche zaehlen je nach Format nicht. Wie genau
verglichen wird (raw = binaer gleich, text, python ...), legt die
Patchdatei je Datei fest. Bei Python zaehlt die Einrueckung (Tiefe,
Tabs/Leerzeichen) immer exakt. Im Patch steht die Einrueckung als
Tabs je Ebene; ein Parameter legt fest, woraus eine Ebene in der Datei
besteht (z.B. 4 Leerzeichen). Jeder
angewendete Patch laesst sich wieder zuruecknehmen. Windows-Zeilenenden
(CRLF) werden erkannt und beibehalten.

Voraussetzungen
---------------
Python 3.8 oder neuer, keine weiteren Pakete.

Typischer Ablauf
----------------
Entwicklung:
  patcher.py create alter_stand/ neuer_stand/ -o 0002-fix.ppatch.json
oder Entwurf (JSON ohne Hashes) schreiben und versiegeln:
  patcher.py seal entwurf.json --base projekt/ -o 0002-fix.ppatch.json

Zielumgebung:
  patcher.py doctor   -p projekt/          (Umgebung pruefen, einmalig)
  patcher.py init     -p projekt/          (einmalig)
  patcher.py check    0002-fix.ppatch.json -p projekt/   (Probelauf)
  patcher.py apply    0002-fix.ppatch.json -p projekt/
  patcher.py status   -p projekt/
  patcher.py rollback -p projekt/          (letzten Patch zuruecknehmen)

Alle Befehle: siehe manifest.txt, Abschnitt 13.

Dokumentation
-------------
  readme.txt     diese Kurzanleitung
  manifest.txt   ausfuehrliche Programmbeschreibung / Konzept
  changelog.txt  Aenderungshistorie
  request.txt    Wuensche fuer kuenftige Aenderungen
