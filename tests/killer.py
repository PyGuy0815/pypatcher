"""Hilfsprogramm fuer Abbruch-Tests: fuehrt patcher.py aus und beendet den
Prozess hart (os._exit) beim n-ten Schreibvorgang - vor oder nach dem
Schreiben (PP_KILL_AT=n, PP_KILL_WHEN=before|after)."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pypatcher import cli, fileio  # noqa: E402

LIMIT = int(os.environ["PP_KILL_AT"])
AFTER = os.environ.get("PP_KILL_WHEN") == "after"
COUNT = [0]
ORIGINAL = fileio.atomic_write


def atomic_write(*args, **kwargs):
    COUNT[0] += 1
    if COUNT[0] == LIMIT and not AFTER:
        os._exit(99)
    result = ORIGINAL(*args, **kwargs)
    if COUNT[0] == LIMIT and AFTER:
        os._exit(99)
    return result


fileio.atomic_write = atomic_write
sys.exit(cli.main(sys.argv[1:]))
