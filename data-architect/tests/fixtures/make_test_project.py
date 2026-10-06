"""Generator testowej solucji SSDT „Sklep” z historią git (dane: sklep.txt obok tego pliku).

Użycie: uv run python tests/fixtures/make_test_project.py <katalog> --phase A|B
    A  tworzy repozytorium i historię do 10 wrz 2026 (stan „przed odświeżeniem”, tag v2.3)
    B  dopisuje commity z drugiej połowy września 2026 (zmiany do wykrycia przez odświeżenie)

Format sklep.txt (bloki rozpoczynane znacznikiem „### ”):
    ### phase A|B                      faza, do której należą kolejne operacje
    ### write <ścieżka>                treść pliku do następnego znacznika (zapisywana z końcami linii CRLF)
    ### remove <ścieżka>
    ### commit <data> <autor> <opis>   autor to skrót z AUTHORS
    ### tag <nazwa>
"""

import shutil
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import git

DATA = Path(__file__).with_name("sklep.txt")

AUTHORS = {
    "mz": ("Marek Zieliński", "marek.zielinski@example.com"),
    "aw": ("Anna Wiśniewska", "anna.wisniewska@example.com"),
    "jn": ("Jan Nowak", "jan.nowak@example.com"),
    "kl": ("Kamil Lewandowski", "kamil.lewandowski@example.com"),
    "pk": ("Piotr Kamiński", "piotr.kaminski@example.com"),
}


@dataclass
class Operation:
    phase: str
    name: str  # write, remove, commit, tag
    argument: str
    content: str | None = None


def parse(text: str) -> list[Operation]:
    """Plik danych → lista operacji."""
    operations: list[Operation] = []
    phase, content_lines = "A", None

    for line in text.split("\n"):
        if line.startswith("### "):
            name, _, argument = line[4:].partition(" ")
            content_lines = None
            if name == "phase":
                phase = argument
                continue
            operations.append(Operation(phase, name, argument))
            if name == "write":
                content_lines = []
                operations[-1].content = content_lines  # uzupełniane kolejnymi liniami

        elif content_lines is not None:
            content_lines.append(line)

        elif line and not line.startswith("#"):
            raise ValueError(f"Nieoczekiwana linia w danych: {line}")

    # znak nowej linii przed kolejnym znacznikiem nie należy do treści pliku
    for op in operations:
        if op.content is not None:
            op.content = "\n".join(op.content)
    return operations


def make(root: Path, phase: str) -> Path:
    """Tworzy (faza A) albo rozwija (faza B) repozytorium projektu testowego."""
    root = Path(root).resolve()

    if phase == "A":
        shutil.rmtree(root, ignore_errors=True)
        root.mkdir(parents=True)
        repo = git.Repo.init(root, initial_branch="main")
        with repo.config_writer() as config:
            config.set_value("core", "autocrlf", "false")
    else:
        repo = git.Repo(root)

    for op in parse(DATA.read_text(encoding="utf-8")):
        if op.phase != phase:
            continue

        if op.name == "write":
            path = root / op.argument
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(op.content.replace("\n", "\r\n").encode("utf-8"))

        elif op.name == "remove":
            (root / op.argument).unlink(missing_ok=True)

        elif op.name == "commit":
            date, author_key, message = op.argument.split(" ", 2)
            author = git.Actor(*AUTHORS[author_key])
            stamp = datetime.fromisoformat(f"{date}T10:00:00+02:00")
            repo.git.add(A=True)
            repo.index.commit(message, author=author, committer=author, author_date=stamp, commit_date=stamp)

        elif op.name == "tag":
            repo.create_tag(op.argument)

        else:
            raise ValueError(f"Nieznana operacja: {op.name}")

    return root


if __name__ == "__main__":
    args = sys.argv[1:]
    target = Path(args[0]) if args and not args[0].startswith("--") else Path("test-project")
    selected = (args[args.index("--phase") + 1] if "--phase" in args else "A").upper()
    print(f"Faza {selected} gotowa: {make(target, selected)}")
