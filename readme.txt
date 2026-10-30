PATCHER - README
================

Status: KONZEPTPHASE (Version 0.1.0) - das Programm ist noch nicht
implementiert. Die ausfuehrliche Beschreibung steht in manifest.txt.

Was macht der Patcher?
----------------------
Er uebertraegt Aenderungen an einem Projekt mit einer einzigen Patchdatei
(*.ppatch) in eine andere Umgebung - ohne ganze Dateien zu kopieren.
Aenderungen koennen bis auf einzelne Funktionen/Methoden genau beschrieben
werden ("ersetze in Datei X die Funktion Y"). Jede Aenderung wird per
SHA-256-Hash geprueft (Datei und Funktion, vorher und nachher). Jeder
angewendete Patch laesst sich wieder zuruecknehmen.

Voraussetzungen
---------------
Python 3.8 oder neuer, keine weiteren Pakete.

Typischer Ablauf
----------------
Entwicklung:
  patcher.py create alter_stand/ neuer_stand/ -o 0002-fix.ppatch
oder Entwurf von Hand schreiben und versiegeln:
  patcher.py seal entwurf.txt --base projekt/ -o 0002-fix.ppatch

Zielumgebung:
  patcher.py init     -p projekt/          (einmalig)
  patcher.py check    0002-fix.ppatch -p projekt/   (Probelauf)
  patcher.py apply    0002-fix.ppatch -p projekt/
  patcher.py status   -p projekt/
  patcher.py rollback -p projekt/          (letzten Patch zuruecknehmen)

Alle Befehle: siehe manifest.txt, Abschnitt 10.

Dokumentation
-------------
  readme.txt     diese Kurzanleitung
  manifest.txt   ausfuehrliche Programmbeschreibung / Konzept
  changelog.txt  Aenderungshistorie
  request.txt    Wuensche fuer kuenftige Aenderungen
