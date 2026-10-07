"""Test regresji silnika: wynik analizy projektu „Sklep” porównany z zapisanym wzorcem (tests/golden/engine_sklep.json).

Każda zmiana w silniku C# (albo w wersji DacFx), która zmieni obiekty, kolumny, relacje, problemy lub hashe definicji,
oblewa ten test. Jeśli zmiana jest zamierzona, wzorzec odświeża się poleceniem:

    UPDATE_GOLDEN=1 uv run pytest tests/test_engine_golden.py

i przegląda różnicę w pliku wzorca (git diff) przed zatwierdzeniem.
"""

import json
import os
from pathlib import Path

from sqlproj_atlas.project_analysis.engine import run_engine
from sqlproj_atlas.project_analysis.sqlproj import discover_projects, list_files, read_text

from conftest import requires_engine

pytestmark = requires_engine

GOLDEN = Path(__file__).parent / "golden" / "engine_sklep.json"


def normalized_result(root: Path, work_dir: Path) -> dict:
    """Wynik silnika w stałej kolejności, bez pełnych definicji (zmianę definicji pokazuje hash)."""
    files = list_files(root)
    projects = discover_projects(root, files)
    engine_input = [
        {"name": p.name, "dsp": p.dsp, "files": [{"path": f, "content": read_text(root, f)} for f in p.files]}
        for p in projects
    ]
    result = run_engine(engine_input, work_dir)

    normalized = {}
    for project in sorted(result.projects, key=lambda p: p.name):
        normalized[project.name] = {
            "platform": project.platform,
            "objects": sorted(
                (o.model_dump(exclude={"definition"}, by_alias=True) for o in project.objects), key=lambda o: o["key"]
            ),
            "edges": sorted(
                (e.model_dump(by_alias=True) for e in project.edges), key=lambda e: (e["from"], e["to"], e["kind"])
            ),
            "issues": sorted(
                (i.model_dump(by_alias=True) for i in project.issues), key=lambda i: (i["object"], i["kind"], i["ref"])
            ),
            "parseErrors": sorted((e.model_dump(by_alias=True) for e in project.parse_errors), key=lambda e: e["file"]),
        }
    return normalized


def test_engine_output_matches_golden_file(project_a, tmp_path):
    actual = normalized_result(project_a, tmp_path / "work")

    if os.environ.get("UPDATE_GOLDEN") == "1" or not GOLDEN.exists():
        GOLDEN.parent.mkdir(exist_ok=True)
        GOLDEN.write_text(json.dumps(actual, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")

    expected = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert actual.keys() == expected.keys()
    for name in expected:
        for section in expected[name]:
            assert actual[name][section] == expected[name][section], f"{name}: różnica w sekcji „{section}”"


def test_golden_file_covers_the_whole_test_project():
    expected = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert sorted(expected) == ["DWH", "Sales", "Staging", "Tests"]
    assert sum(len(p["objects"]) for p in expected.values()) == 68
    assert sum(len(p["edges"]) for p in expected.values()) == 84
