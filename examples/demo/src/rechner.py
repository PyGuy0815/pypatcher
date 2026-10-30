#!/usr/bin/env python3
"""Kleiner Rechner - Beispielprojekt fuer pypatcher."""

import sys


# addiert zwei Zahlen
def add(a, b):
    return a + b


class Rechner:
    """Rechnet."""

    def run(self, args):
        if len(args) != 2:
            return None
        return add(int(args[0]), int(args[1]))


if __name__ == "__main__":
    print(Rechner().run(sys.argv[1:]))
