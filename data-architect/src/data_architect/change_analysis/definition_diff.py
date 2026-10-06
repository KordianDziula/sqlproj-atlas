"""Różnica definicji SQL „było → jest” liczona z dwóch zapisanych analiz (difflib z biblioteki standardowej).

Wynik to linie [rodzaj, tekst], gdzie rodzaj: "h" nagłówek fragmentu (np. „linie 12–18”), "a" dodana,
"d" usunięta, "c" kontekst. W tej postaci trafiają do UI (zakładka Zmiany).
"""

import difflib

MAX_LINES = 600  # dłuższe różnice obcinamy, żeby nie rozdmuchać odpowiedzi API


def definition_diff(before: str | None, after: str | None, context: int = 3) -> list[list[str]]:
    """Linie różnicy dwóch definicji. Brak definicji „przed” oznacza nowy obiekt, brak „po” usunięty."""
    old = _lines(before)
    new = _lines(after)

    # nowy albo usunięty obiekt: cała definicja jako dodana albo usunięta
    if not old:
        return [["a", line] for line in new][:MAX_LINES]
    if not new:
        return [["d", line] for line in old][:MAX_LINES]

    result = []
    for group in difflib.SequenceMatcher(None, old, new, autojunk=False).get_grouped_opcodes(context):
        first, last = group[0], group[-1]
        result.append(["h", f"linie {first[3] + 1}–{last[4]}"])

        for tag, old_start, old_end, new_start, new_end in group:
            if tag == "equal":
                result += [["c", line] for line in old[old_start:old_end]]
                continue
            result += [["d", line] for line in old[old_start:old_end]]
            result += [["a", line] for line in new[new_start:new_end]]

    return result[:MAX_LINES]


def _lines(text: str | None) -> list[str]:
    return text.replace("\r\n", "\n").replace("\r", "\n").split("\n") if text else []
