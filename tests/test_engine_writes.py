"""Rodzaj relacji procedury z tabelą (silnik C#): `writes` tylko dla tego samego obiektu, w którym następuje zapis.

Zapis rozpoznaje ScriptDom (INSERT, UPDATE, DELETE, MERGE, TRUNCATE), odwołania wylicza DacFx. Obiekt zapisu
i obiekt z relacji muszą się zgadzać bazą (lokalna albo ta sama baza zewnętrzna / zmienna SQLCMD), schematem
i nazwą. Tabela o tej samej nazwie w innym schemacie albo w innej bazie jest tylko czytana.
"""

from sqlproj_atlas.project_analysis.engine import run_engine

from conftest import requires_engine

pytestmark = requires_engine


def edges(tmp_path, **projects: dict[str, str]) -> dict[str, set[tuple]]:
    """Analiza projektów {nazwa: {plik: sql}}; wynik: {projekt: {(od, do, rodzaj, baza zewnętrzna)}}."""
    engine_input = [
        {"name": name, "dsp": None, "files": [{"path": path, "content": sql} for path, sql in files.items()]}
        for name, files in projects.items()
    ]
    result = run_engine(engine_input, tmp_path / "work")
    for project in result.projects:
        assert project.parse_errors == [], project.parse_errors
    return {p.name: {(e.source, e.to, e.kind, e.external_db) for e in p.edges} for p in result.projects}


def from_object(result: set[tuple], source: str) -> set[tuple]:
    return {(to, kind, db) for src, to, kind, db in result if src == source}


def test_insert_from_table_with_same_name_in_other_database(tmp_path):
    # przypadek z ShopDW: stg.Order zasilana z [$(Src)].sales.Order (ta sama nazwa tabeli, inna baza i schemat)
    result = edges(
        tmp_path,
        Src={
            "sales.sql": "CREATE SCHEMA [sales];",
            "Order.sql": "CREATE TABLE [sales].[Order] ([Id] INT NOT NULL PRIMARY KEY, [Total] MONEY NULL);",
        },
        Dw={
            "stg.sql": "CREATE SCHEMA [stg];",
            "etl.sql": "CREATE SCHEMA [etl];",
            "Order.sql": "CREATE TABLE [stg].[Order] ([Id] INT NOT NULL, [Total] MONEY NULL);",
            "usp_LoadStaging.sql": """
CREATE PROCEDURE [etl].[usp_LoadStaging]
AS
BEGIN
    TRUNCATE TABLE [stg].[Order];
    INSERT INTO [stg].[Order] ([Id], [Total])
    SELECT o.[Id], o.[Total] FROM [$(Src)].[sales].[Order] AS o;
END""",
        },
    )

    assert from_object(result["Dw"], "etl.usp_loadstaging") == {
        ("stg.order", "writes", None),
        ("sales.order", "reads", "$(Src)"),
    }


def test_insert_into_other_database_from_local_table_with_same_name(tmp_path):
    # odwrotnie: zapis do innej bazy nie oznacza zapisu do lokalnej tabeli o tym samym schemacie i nazwie
    result = edges(
        tmp_path,
        Dw={
            "sales.sql": "CREATE SCHEMA [sales];",
            "Order.sql": "CREATE TABLE [sales].[Order] ([Id] INT NOT NULL PRIMARY KEY);",
            "usp_Export.sql": """
CREATE PROCEDURE [sales].[usp_Export]
AS
    INSERT INTO [$(Archive)].[sales].[Order] ([Id]) SELECT [Id] FROM [sales].[Order];""",
        },
    )

    assert from_object(result["Dw"], "sales.usp_export") == {
        ("sales.order", "reads", None),
        ("sales.order", "writes", "$(Archive)"),
    }


def test_insert_from_table_with_same_name_in_other_schema(tmp_path):
    result = edges(
        tmp_path,
        Shop={
            "sales.sql": "CREATE SCHEMA [sales];",
            "archive.sql": "CREATE SCHEMA [archive];",
            "Order.sql": "CREATE TABLE [sales].[Order] ([Id] INT NOT NULL PRIMARY KEY);",
            "ArchiveOrder.sql": "CREATE TABLE [archive].[Order] ([Id] INT NOT NULL PRIMARY KEY);",
            "usp_Archive.sql": """
CREATE PROCEDURE [archive].[usp_Archive]
AS
    INSERT INTO [archive].[Order] ([Id]) SELECT o.[Id] FROM [sales].[Order] AS o;""",
        },
    )

    assert from_object(result["Shop"], "archive.usp_archive") == {
        ("archive.order", "writes", None),
        ("sales.order", "reads", None),
    }


def test_target_without_schema_means_default_schema(tmp_path):
    # INSERT INTO Orders: domyślny schemat (dbo) jak w DacFx, a nie dowolna tabela o nazwie Orders
    result = edges(
        tmp_path,
        Shop={
            "hist.sql": "CREATE SCHEMA [hist];",
            "Orders.sql": "CREATE TABLE [dbo].[Orders] ([Id] INT NOT NULL PRIMARY KEY);",
            "HistOrders.sql": "CREATE TABLE [hist].[Orders] ([Id] INT NOT NULL PRIMARY KEY);",
            "usp_Restore.sql": """
CREATE PROCEDURE [dbo].[usp_Restore]
AS
    INSERT INTO Orders (Id) SELECT h.Id FROM [hist].[Orders] AS h;""",
        },
    )

    assert from_object(result["Shop"], "dbo.usp_restore") == {
        ("dbo.orders", "writes", None),
        ("hist.orders", "reads", None),
    }


def test_update_and_delete_through_alias(tmp_path):
    result = edges(
        tmp_path,
        Shop={
            "sales.sql": "CREATE SCHEMA [sales];",
            "Orders.sql": "CREATE TABLE [sales].[Orders] ([Id] INT NOT NULL PRIMARY KEY, [Status] NVARCHAR(20) NULL);",
            "Blocked.sql": "CREATE TABLE [sales].[Blocked] ([OrderId] INT NOT NULL PRIMARY KEY);",
            "usp_Cleanup.sql": """
CREATE PROCEDURE [sales].[usp_Cleanup]
AS
BEGIN
    UPDATE o SET o.[Status] = N'blocked'
    FROM [sales].[Orders] AS o
    INNER JOIN [sales].[Blocked] AS b ON b.[OrderId] = o.[Id];

    DELETE b FROM [sales].[Blocked] AS b WHERE b.[OrderId] < 0;
END""",
        },
    )

    assert from_object(result["Shop"], "sales.usp_cleanup") == {
        ("sales.orders", "writes", None),
        ("sales.blocked", "writes", None),
    }


def test_read_and_write_of_same_table_is_one_write(tmp_path):
    result = edges(
        tmp_path,
        Shop={
            "sales.sql": "CREATE SCHEMA [sales];",
            "Orders.sql": "CREATE TABLE [sales].[Orders] ([Id] INT NOT NULL PRIMARY KEY, [Total] MONEY NULL);",
            "usp_Fix.sql": """
CREATE PROCEDURE [sales].[usp_Fix]
AS
    UPDATE [sales].[Orders] SET [Total] = 0
    WHERE [Id] IN (SELECT [Id] FROM [sales].[Orders] WHERE [Total] < 0);""",
        },
    )

    assert from_object(result["Shop"], "sales.usp_fix") == {("sales.orders", "writes", None)}


def test_table_variables_and_temp_tables_are_ignored(tmp_path):
    result = edges(
        tmp_path,
        Shop={
            "Orders.sql": "CREATE TABLE [dbo].[Orders] ([Id] INT NOT NULL PRIMARY KEY);",
            "usp_Temp.sql": """
CREATE PROCEDURE [dbo].[usp_Temp]
AS
BEGIN
    DECLARE @ids TABLE ([Id] INT);
    CREATE TABLE #work ([Id] INT);
    INSERT INTO @ids ([Id]) SELECT [Id] FROM [dbo].[Orders];
    INSERT INTO #work ([Id]) SELECT [Id] FROM @ids;
    TRUNCATE TABLE #work;
END""",
        },
    )

    assert from_object(result["Shop"], "dbo.usp_temp") == {("dbo.orders", "reads", None)}
