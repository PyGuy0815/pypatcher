"""Ablaufsteuerung im Patch: PRINT, INPUT, IF/ELIF/ELSE/END, LABEL/GOTO,
SET, EXIT (manifest.txt Abschnitt 6.8).

Zuerst wird der Ablauf vollstaendig bestimmt (Fragen beantwortet, Zweige
gewaehlt); das Ergebnis ist eine lineare Liste von Dateibefehlen. Erst diese
wird geprueft und atomar angewendet - eine Entscheidung kann also nie ein
halb gepatchtes Projekt hinterlassen.
"""

import re

from .errors import PatchInvalid, UsageError

MAX_STEPS = 10000
MAX_PATHS = 64
VAR_RE = re.compile(r"\$\{([A-Za-z_]\w*)\}")
NO = ("", "0", "nein", "no", "false", "falsch", "n")


class FlowExit(Exception):
    """EXIT im Patch: Abbruch ohne Aenderung."""


class _Need(Exception):
    def __init__(self, item):
        Exception.__init__(self, item["var"])
        self.item = item


def subst(text, values):
    return VAR_RE.sub(lambda m: values.get(m.group(1), ""), text)


def structure(flow):
    """IF-Bloecke zuordnen und Sprungmarken pruefen."""
    stack, branches, labels = [], {}, {}
    for i, item in enumerate(flow):
        stmt = item["stmt"]
        if stmt == "if":
            stack.append([i])
        elif stmt in ("elif", "else"):
            if not stack:
                raise PatchInvalid("%s ohne IF" % stmt.upper())
            if flow[stack[-1][-1]]["stmt"] == "else":
                raise PatchInvalid("%s nach ELSE" % stmt.upper())
            stack[-1].append(i)
        elif stmt == "end":
            if not stack:
                raise PatchInvalid("END ohne IF")
            block = stack.pop() + [i]
            for k in block[:-1]:
                branches[k] = block
        elif stmt == "label":
            if item["name"] in labels:
                raise PatchInvalid("LABEL %s doppelt" % item["name"])
            labels[item["name"]] = i
    if stack:
        raise PatchInvalid("IF ohne END")
    for item in flow:
        if item["stmt"] == "goto" and item["name"] not in labels:
            raise PatchInvalid("GOTO %s: LABEL fehlt" % item["name"])
    return branches, labels


def _cond(tokens, values):
    value = values.get(tokens[0], "")
    if len(tokens) == 1:
        return value.strip().lower() not in NO
    op, other = tokens[1], subst(tokens[2], values)
    if op == "==":
        return value == other
    if op == "!=":
        return value != other
    return value in [v.strip() for v in other.split(",")]


def plan(patch, ask, say=None, preset=None):
    """Ablauf ausfuehren -> (Dateibefehle in Ausfuehrungsreihenfolge, Antworten).

    ask(item) liefert die Antwort auf INPUT, say(text) gibt PRINT aus,
    preset sind vorgegebene Antworten (--set).
    """
    flow = patch.get("flow")
    if not flow:
        return list(patch["files"]), {}
    branches, labels = structure(flow)
    values = dict(preset or {})
    answers, entries, done, taken = {}, [], set(), set()
    pc, steps = 0, 0
    while pc < len(flow):
        steps += 1
        if steps > MAX_STEPS:
            raise PatchInvalid("Ablauf endet nicht (mehr als %d Schritte - Endlosschleife?)"
                               % MAX_STEPS)
        item = flow[pc]
        stmt = item["stmt"]
        if stmt == "file":
            if item["index"] in done:
                raise PatchInvalid("Dateibefehl %d wuerde zweimal ausgefuehrt (GOTO-Schleife)"
                                   % (item["index"] + 1))
            done.add(item["index"])
            entries.append(patch["files"][item["index"]])
        elif stmt == "print":
            if say:
                say(subst(item["text"], values))
        elif stmt == "input":
            var = item["var"]
            if var not in (preset or {}):
                values[var] = ask(item)
            if item["choices"] and values[var] not in item["choices"]:
                raise UsageError("Antwort %r fuer %s ist keine der Moeglichkeiten: %s"
                                 % (values[var], var, ", ".join(item["choices"])))
            answers[var] = values[var]
        elif stmt == "set":
            values[item["var"]] = subst(item["value"], values)
        elif stmt == "exit":
            raise FlowExit(subst(item["text"], values) or "Abbruch durch den Patch (EXIT)")
        elif stmt == "goto":
            pc = labels[item["name"]]
            continue
        elif stmt in ("if", "elif"):
            block = branches[pc]
            if stmt == "if":
                taken.discard(block[0])
            elif block[0] in taken:        # vorheriger Zweig ausgefuehrt -> hinter END
                pc = block[-1] + 1
                continue
            if _cond(item["cond"], values):
                taken.add(block[0])
                pc += 1
            else:
                pc = block[block.index(pc) + 1]
                if flow[pc]["stmt"] == "else":
                    pc += 1
            continue
        elif stmt == "else":               # nur nach einem ausgefuehrten Zweig erreichbar
            pc = branches[pc][-1]
        pc += 1
    return entries, answers


def explore(patch):
    """Alle Ablaeufe fuer seal: jede Kombination der INPUT-Moeglichkeiten.

    -> Liste (Antworten, Indizes der ausgefuehrten Dateibefehle). Ablaeufe mit
    EXIT entfallen. Bedingungen auf INPUT ohne choices sind nicht erlaubt.
    """
    flow = patch.get("flow")
    if not flow:
        return [({}, list(range(len(patch["files"]))))]
    cond_vars = set(i["cond"][0] for i in flow if i["stmt"] in ("if", "elif"))
    for item in flow:
        if item["stmt"] == "input" and not item["choices"] and item["var"] in cond_vars:
            raise PatchInvalid("seal: INPUT %s wird in IF verwendet und braucht choices="
                               % item["var"])
    results, todo = [], [{}]
    while todo:
        fixed = todo.pop()

        def ask(item):
            if not item["choices"]:
                return item.get("default") or ""
            raise _Need(item)
        try:
            entries, answers = plan(patch, ask, preset=fixed)
        except _Need as need:
            for choice in reversed(need.item["choices"]):
                todo.append(dict(fixed, **{need.item["var"]: choice}))
            continue
        except FlowExit:
            continue
        index = dict((id(e), k) for k, e in enumerate(patch["files"]))
        results.append((answers, [index[id(e)] for e in entries]))
        if len(results) > MAX_PATHS:
            raise PatchInvalid("seal: mehr als %d moegliche Ablaeufe" % MAX_PATHS)
    if not results:
        raise PatchInvalid("jeder Ablauf endet mit EXIT")
    return results
