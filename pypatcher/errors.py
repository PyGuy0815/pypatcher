"""Fehlerklassen; exit_code entspricht manifest.txt Abschnitt 13."""


class PatcherError(Exception):
    exit_code = 5


class UsageError(PatcherError):
    exit_code = 1


class PatchInvalid(PatcherError):
    exit_code = 2


class Conflict(PatcherError):
    exit_code = 3


class JournalError(PatcherError):
    exit_code = 4


class TestsFailed(PatcherError):
    exit_code = 6
