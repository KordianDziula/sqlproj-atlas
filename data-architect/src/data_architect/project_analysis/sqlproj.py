"""Odczyt projektów SSDT: pliki .sqlproj (format klasyczny i SDK-style) oraz .refactorlog.

Plik .sqlproj to projekt MSBuild (XML). Interesuje nas:
    - nazwa projektu (<Name>) i platforma (<DSP>),
    - pliki modelu: <Build Include>; w SDK-style domyślnie wszystkie *.sql minus <Build Remove>, <None>, skrypty deploy,
    - skrypty <PreDeploy>/<PostDeploy> (nie należą do modelu bazy),
    - zmienne SQLCMD (<SqlCmdVariable>) i referencje do innych projektów/dacpaców (odwołania między bazami),
    - plik .refactorlog (historia zmian nazw z SSDT).
"""

import codecs
import logging
import os
import posixpath
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

from wcmatch import glob

from data_architect.core.settings import SKIP_DIRS

log = logging.getLogger(__name__)

# flagi wzorców MSBuild: „**” obejmuje podkatalogi, wielkość liter bez znaczenia, ścieżki z „/”
_GLOB_FLAGS = glob.GLOBSTAR | glob.IGNORECASE | glob.FORCEUNIX


# ---------------------------------------------------------------------------
# Model projektu
# ---------------------------------------------------------------------------


@dataclass
class SqlCmdVariable:
    name: str
    default_value: str = ""
    value: str = ""


@dataclass
class ProjectReference:
    """Referencja do innego projektu (kind="project") albo pliku .dacpac (kind="dacpac")."""

    kind: str
    path: str
    name: str
    variable: str | None = None  # zmienna SQLCMD z nazwą bazy, np. "Staging" dla [$(Staging)]
    literal: str | None = None  # stała nazwa bazy, np. "master"


@dataclass
class SqlProject:
    name: str
    file: str  # ścieżka .sqlproj względem katalogu głównego
    dir: str  # katalog projektu ("" = katalog główny)
    format: str  # "sdk" albo "classic"
    dsp: str | None
    files: list[str]  # pliki .sql modelu bazy
    pre_deploy: list[str] = field(default_factory=list)
    post_deploy: list[str] = field(default_factory=list)
    sqlcmd: list[SqlCmdVariable] = field(default_factory=list)
    references: list[ProjectReference] = field(default_factory=list)
    refactorlog: str | None = None
    renames: list[dict] = field(default_factory=list)  # wypełniane z .refactorlog przy analizie

    def info(self) -> dict:
        """Metadane zapisywane w bazie (tabela project.info) i pokazywane w UI."""
        return {
            "dsp": self.dsp,
            "sqlcmd": [{"name": v.name, "defaultValue": v.default_value, "value": v.value} for v in self.sqlcmd],
            "references": [vars(r) for r in self.references],
            "renames": self.renames,
            "files": len(self.files),
            "preDeploy": self.pre_deploy,
            "postDeploy": self.post_deploy,
        }


# ---------------------------------------------------------------------------
# Pliki katalogu roboczego
# ---------------------------------------------------------------------------


def list_files(root: Path) -> list[str]:
    """Wszystkie pliki projektu jako ścieżki względne z „/”, bez katalogów technicznych (.git, bin, obj, ...)."""
    found = []
    for dirpath, dirnames, filenames in os.walk(root):
        # przycinamy katalogi techniczne, żeby nie schodzić np. do node_modules
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)

        rel_dir = Path(dirpath).relative_to(root).as_posix()
        for name in sorted(filenames):
            found.append(name if rel_dir == "." else f"{rel_dir}/{name}")

    return found


# znaczniki kodowania na początku pliku; Visual Studio zapisuje pliki SSDT z BOM (UTF-8, w starszych projektach UTF-16)
_BOMS = ((codecs.BOM_UTF8, "utf-8"), (codecs.BOM_UTF16_LE, "utf-16-le"), (codecs.BOM_UTF16_BE, "utf-16-be"))


def read_text(root: Path, rel: str) -> str:
    """Treść pliku źródłowego bez znacznika BOM i bez zmiany końców linii (silnik liczy z niej hash definicji).

    Znacznik BOM musi zniknąć: silnik potraktowałby go jako część pierwszego słowa i zgłosił błąd składni.
    Plik bez znacznika czytamy jako UTF-8 (niepoprawne bajty zastępuje znak �).
    """
    data = (root / rel).read_bytes()
    for bom, encoding in _BOMS:
        if data.startswith(bom):
            return data[len(bom) :].decode(encoding, errors="replace")
    return data.decode("utf-8", errors="replace")


# ---------------------------------------------------------------------------
# Wykrywanie projektów
# ---------------------------------------------------------------------------


def discover_projects(root: Path, files: list[str] | None = None) -> list[SqlProject]:
    """Znajduje i parsuje wszystkie projekty .sqlproj w katalogu `root`."""
    files = files if files is not None else list_files(root)
    project_files = [f for f in files if f.lower().endswith(".sqlproj")]
    project_dirs = [_dirname(f) for f in project_files]

    projects = []
    for project_file in project_files:
        try:
            xml = ET.fromstring((root / project_file).read_bytes())
        except (OSError, ET.ParseError) as e:
            log.warning("Pominięto %s: %s", project_file, e)
            continue

        projects.append(_parse_project(project_file, xml, files, project_dirs))

    return sorted(projects, key=lambda p: p.name.lower())


def _parse_project(project_file: str, xml: ET.Element, files: list[str], project_dirs: list[str]) -> SqlProject:
    project_dir = _dirname(project_file)
    is_sdk = "Sdk" in xml.attrib or _first(xml, "Sdk") is not None

    # pliki zagnieżdżonych projektów nie należą do tego projektu
    nested = [d for d in project_dirs if d != project_dir and (project_dir == "" or d.startswith(project_dir + "/"))]

    def owns(path: str) -> bool:
        inside = project_dir == "" or path.startswith(project_dir + "/")
        return inside and not any(path.startswith(d + "/") for d in nested)

    def relative(path: str) -> str:
        return path[len(project_dir) + 1 :] if project_dir else path

    def items(tag: str, attr: str = "Include") -> list[tuple[str, dict[str, str]]]:
        """Elementy <tag attr="..."> z metadanymi (elementy podrzędne)."""
        return [(el.get(attr), _metadata(el)) for el in _all(xml, tag) if el.get(attr) is not None]

    def joined(tag: str) -> list[str]:
        return [_join(project_dir, value) for value, _ in items(tag)]

    # --- skrypty deploy i pliki wyłączone ---
    pre_deploy, post_deploy = joined("PreDeploy"), joined("PostDeploy")
    deploy_scripts = {p.lower() for p in pre_deploy + post_deploy}

    # --- pliki modelu bazy ---
    if is_sdk:
        # SDK-style: wszystkie *.sql projektu, poza <Build Remove>, <None> i skryptami deploy
        removed = [pattern for pattern, _ in items("Build", "Remove")]
        excluded = {p.lower() for p in joined("None")} | deploy_scripts
        sql_files = [
            f
            for f in files
            if owns(f)
            and f.lower().endswith(".sql")
            and f.lower() not in excluded
            and not any(_glob_match(relative(f), pattern) for pattern in removed)
        ]
        for value, _ in items("Build"):
            path = _join(project_dir, value)
            if "*" not in value and path in files and path not in sql_files:
                sql_files.append(path)
    else:
        # klasyczny: tylko pliki wymienione w <Build Include> (także wzorce)
        sql_files = []
        for value, _ in items("Build"):
            if "*" in value:
                sql_files += [f for f in files if owns(f) and _glob_match(relative(f), value)]
            elif _join(project_dir, value) in files:
                sql_files.append(_join(project_dir, value))

    sql_files = sorted({f for f in sql_files if f.lower() not in deploy_scripts})

    # --- zmienne SQLCMD i referencje ---
    sqlcmd = [
        SqlCmdVariable(name, meta.get("DefaultValue", ""), meta.get("Value", ""))
        for name, meta in items("SqlCmdVariable")
    ]
    references = [
        ProjectReference(
            kind="project",
            path=_join(project_dir, value),
            name=meta.get("Name") or _stem(value, ".sqlproj"),
            variable=meta.get("DatabaseSqlCmdVariable") or None,
            literal=meta.get("DatabaseVariableLiteralValue") or None,
        )
        for value, meta in items("ProjectReference")
    ] + [
        ProjectReference(
            kind="dacpac",
            path=_normalize(value),
            name=_stem(value, ".dacpac"),
            variable=meta.get("DatabaseSqlCmdVariable") or None,
            literal=meta.get("DatabaseVariableLiteralValue") or None,
        )
        for value, meta in items("ArtifactReference")
    ]

    # --- .refactorlog: wskazany w projekcie albo pierwszy znaleziony w jego katalogu ---
    refactorlogs = joined("RefactorLog")
    refactorlog = refactorlogs[0] if refactorlogs else None
    if refactorlog is None:
        refactorlog = next((f for f in files if owns(f) and f.lower().endswith(".refactorlog")), None)

    return SqlProject(
        name=_text(xml, "Name") or _stem(project_file, ".sqlproj"),
        file=project_file,
        dir=project_dir,
        format="sdk" if is_sdk else "classic",
        dsp=_text(xml, "DSP"),
        files=sql_files,
        pre_deploy=pre_deploy,
        post_deploy=post_deploy,
        sqlcmd=sqlcmd,
        references=references,
        refactorlog=refactorlog,
    )


# ---------------------------------------------------------------------------
# .refactorlog
# ---------------------------------------------------------------------------


def parse_refactor_log(text: str) -> list[dict]:
    """Zmiany nazw zapisane przez SSDT: [{element: "[pay].[Transactions]", newName: "[PaymentTransactions]", type}]."""
    try:
        xml = ET.fromstring(text)
    except ET.ParseError:
        return []

    renames = []
    for operation in _all(xml, "Operation"):
        if operation.get("Name") != "Rename Refactor":
            continue

        props = {p.get("Name"): p.get("Value") for p in _all(operation, "Property")}
        if props.get("ElementName") and props.get("NewName"):
            renames.append(
                {"element": props["ElementName"], "newName": props["NewName"], "type": props.get("ElementType")}
            )

    return renames


# ---------------------------------------------------------------------------
# Pomocnicze: XML bez przestrzeni nazw, ścieżki, wzorce
# ---------------------------------------------------------------------------


def _local(tag: str) -> str:
    """Nazwa elementu bez przestrzeni nazw MSBuild ("{http://...}Build" → "Build")."""
    return tag.rsplit("}", 1)[-1]


def _all(xml: ET.Element, tag: str) -> list[ET.Element]:
    return [el for el in xml.iter() if _local(el.tag) == tag]


def _first(xml: ET.Element, tag: str) -> ET.Element | None:
    return next(iter(_all(xml, tag)), None)


def _text(xml: ET.Element, tag: str) -> str | None:
    """Tekst pierwszego elementu <tag> (np. <Name>), bez białych znaków; None, gdy brak."""
    el = _first(xml, tag)
    if el is None or not el.text:
        return None
    return el.text.strip() or None


def _metadata(el: ET.Element) -> dict[str, str]:
    return {_local(child.tag): (child.text or "").strip() for child in el}


def _normalize(path: str) -> str:
    path = path.replace("\\", "/")
    return path[2:] if path.startswith("./") else path


def _join(base: str, rel: str) -> str:
    """Łączy katalog projektu ze ścieżką z .sqlproj i rozwiązuje „..”."""
    parts: list[str] = []
    for part in (f"{base}/{_normalize(rel)}" if base else _normalize(rel)).split("/"):
        if part == "..":
            if parts:
                parts.pop()
        elif part and part != ".":
            parts.append(part)
    return "/".join(parts)


def _dirname(path: str) -> str:
    return posixpath.dirname(path)


def _stem(path: str, extension: str) -> str:
    name = posixpath.basename(_normalize(path))
    return name[: -len(extension)] if name.lower().endswith(extension) else name


def _glob_match(path: str, pattern: str) -> bool:
    return glob.globmatch(path, _normalize(pattern), flags=_GLOB_FLAGS)
