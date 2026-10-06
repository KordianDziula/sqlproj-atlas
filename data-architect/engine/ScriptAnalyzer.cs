using Microsoft.SqlServer.TransactSql.ScriptDom;

namespace AtlasEngine;

/// <summary>Informacje z ScriptDom o jednym obiekcie (CREATE ...): co zapisuje, co wywołuje, dynamiczny SQL, OPENQUERY.</summary>
public sealed class ScriptObject
{
    public string Key = "";
    public string File = "";
    public string Text = "";          // tekst definicji obiektu
    public int StartLine;              // linia początku definicji w pliku
    public readonly HashSet<string> Writes = new(StringComparer.OrdinalIgnoreCase);
    public readonly List<(int Line, string Text)> Dynamic = new();
    public readonly List<(int Line, string Server, string Query)> OpenQueries = new();
}

public static class ScriptAnalyzer
{
    /// <summary>Parsuje plik; zwraca obiekty zdefiniowane w pliku lub błędy składni.</summary>
    public static List<ScriptObject> Analyze(FileInput file, List<ParseError> errors)
    {
        var parser = new TSql160Parser(initialQuotedIdentifiers: true);
        var fragment = parser.Parse(new StringReader(file.Content), out var parseErrors);
        if (parseErrors.Count > 0)
        {
            foreach (var e in parseErrors.Take(5))
                errors.Add(new ParseError { File = file.Path, Line = e.Line, Message = e.Message });
            return new List<ScriptObject>();
        }
        var result = new List<ScriptObject>();
        if (fragment is not TSqlScript script) return result;
        foreach (var batch in script.Batches)
        foreach (var stmt in batch.Statements)
        {
            var name = CreatedName(stmt);
            if (name == null) continue;
            var so = new ScriptObject
            {
                Key = Names.Key(name.SchemaIdentifier?.Value, name.BaseIdentifier.Value),
                File = file.Path,
                StartLine = stmt.StartLine,
                Text = stmt.StartOffset >= 0 && stmt.StartOffset + stmt.FragmentLength <= file.Content.Length
                    ? file.Content.Substring(stmt.StartOffset, stmt.FragmentLength) : "",
            };
            stmt.Accept(new BodyVisitor(so, file.Content));
            result.Add(so);
        }
        // indeksy i ALTER TABLE w tym samym pliku należą do definicji tabeli (zmiana indeksu = zmiana tabeli)
        foreach (var batch in script.Batches)
        foreach (var stmt in batch.Statements)
        {
            var target = stmt switch
            {
                CreateIndexStatement ci => ci.OnName,
                CreateColumnStoreIndexStatement cs => cs.OnName,
                AlterTableStatement at => at.SchemaObjectName,
                _ => null,
            };
            if (target == null) continue;
            var key = Names.Key(target.SchemaIdentifier?.Value, target.BaseIdentifier.Value);
            var owner = result.FirstOrDefault(r => r.Key == key);
            if (owner == null || stmt.StartOffset < 0 || stmt.StartOffset + stmt.FragmentLength > file.Content.Length) continue;
            owner.Text += "\nGO\n" + file.Content.Substring(stmt.StartOffset, stmt.FragmentLength);
        }
        return result;
    }

    static SchemaObjectName? CreatedName(TSqlStatement s) => s switch
    {
        CreateProcedureStatement p => p.ProcedureReference?.Name,
        CreateOrAlterProcedureStatement p => p.ProcedureReference?.Name,
        CreateViewStatement v => v.SchemaObjectName,
        CreateOrAlterViewStatement v => v.SchemaObjectName,
        CreateFunctionStatement f => f.Name,
        CreateOrAlterFunctionStatement f => f.Name,
        CreateTriggerStatement t => t.Name,
        CreateOrAlterTriggerStatement t => t.Name,
        CreateTableStatement t => t.SchemaObjectName,
        _ => null,
    };

    sealed class BodyVisitor : TSqlFragmentVisitor
    {
        readonly ScriptObject _so;
        readonly string _content;
        public BodyVisitor(ScriptObject so, string content) { _so = so; _content = content; }

        void Write(TableReference? target, FromClause? from)
        {
            if (target is not NamedTableReference nt) return;
            var n = nt.SchemaObject;
            // UPDATE o SET ... FROM sales.Orders o  →  cel to alias, szukamy prawdziwej tabeli
            if (n.SchemaIdentifier == null && from != null)
            {
                foreach (var tr in Flatten(from.TableReferences))
                    if (tr is NamedTableReference a && a.Alias != null &&
                        string.Equals(a.Alias.Value, n.BaseIdentifier.Value, StringComparison.OrdinalIgnoreCase))
                    { AddWrite(a.SchemaObject); return; }
            }
            AddWrite(n);
        }

        void AddWrite(SchemaObjectName n)
        {
            if (n.BaseIdentifier.Value.StartsWith('#') || n.BaseIdentifier.Value.StartsWith('@')) return;
            _so.Writes.Add(n.BaseIdentifier.Value);
            _so.Writes.Add(Names.Key(n.SchemaIdentifier?.Value, n.BaseIdentifier.Value));
        }

        static IEnumerable<TableReference> Flatten(IEnumerable<TableReference> refs)
        {
            foreach (var r in refs)
            {
                yield return r;
                if (r is JoinTableReference j)
                    foreach (var x in Flatten(new[] { j.FirstTableReference, j.SecondTableReference })) yield return x;
            }
        }

        public override void ExplicitVisit(InsertStatement node)
        { Write(node.InsertSpecification?.Target, null); base.ExplicitVisit(node); }

        public override void ExplicitVisit(UpdateStatement node)
        { Write(node.UpdateSpecification?.Target, node.UpdateSpecification?.FromClause); base.ExplicitVisit(node); }

        public override void ExplicitVisit(DeleteStatement node)
        { Write(node.DeleteSpecification?.Target, node.DeleteSpecification?.FromClause); base.ExplicitVisit(node); }

        public override void ExplicitVisit(MergeStatement node)
        { Write(node.MergeSpecification?.Target, null); base.ExplicitVisit(node); }

        public override void ExplicitVisit(TruncateTableStatement node)
        { if (node.TableName != null) AddWrite(node.TableName); base.ExplicitVisit(node); }

        public override void ExplicitVisit(ExecuteStatement node)
        {
            var entity = node.ExecuteSpecification?.ExecutableEntity;
            if (entity is ExecutableStringList)
                AddDynamic(node);
            else if (entity is ExecutableProcedureReference pr)
            {
                var name = pr.ProcedureReference?.ProcedureReference?.Name?.BaseIdentifier?.Value;
                if (pr.ProcedureReference?.ProcedureVariable != null) AddDynamic(node);
                else if (name != null && name.Equals("sp_executesql", StringComparison.OrdinalIgnoreCase)) AddDynamic(node);
            }
            base.ExplicitVisit(node);
        }

        public override void ExplicitVisit(OpenQueryTableReference node)
        {
            _so.OpenQueries.Add((node.StartLine, node.LinkedServer?.Value ?? "", node.Query?.Value ?? ""));
            base.ExplicitVisit(node);
        }

        void AddDynamic(TSqlFragment node)
        {
            var text = node.StartOffset >= 0 && node.StartOffset + node.FragmentLength <= _content.Length
                ? _content.Substring(node.StartOffset, node.FragmentLength) : "EXEC";
            _so.Dynamic.Add((node.StartLine, text.Length > 300 ? text[..300] + "…" : text));
        }
    }
}

public static class Names
{
    public static string Clean(string? s) => (s ?? "").Trim().Trim('[', ']', '"');

    /// <summary>Klucz obiektu: schemat.nazwa małymi literami, domyślny schemat dbo.</summary>
    public static string Key(string? schema, string name)
    {
        var sc = Clean(schema);
        return ((sc.Length == 0 ? "dbo" : sc) + "." + Clean(name)).ToLowerInvariant();
    }
}
