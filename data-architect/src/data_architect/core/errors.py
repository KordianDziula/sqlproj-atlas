"""Błędy domenowe.

`AtlasError` to błąd, którego treść ma zobaczyć Claude albo użytkownik (np. „Brak analizy”).
Warstwa MCP zamienia go na `ToolError`, a API webowe na odpowiedź 400 z polem `error`.
Każdy inny wyjątek to błąd programu: trafia do logu, a na zewnątrz idzie tylko ogólny komunikat.
"""


class AtlasError(Exception):
    """Błąd z komunikatem przeznaczonym dla Claude'a lub użytkownika."""
