using System.Security.Cryptography;
using System.Text;
using System.Text.RegularExpressions;
using Microsoft.SqlServer.Dac.Model;

namespace AtlasEngine;

/// <summary>Buduje model DacFx z plików jednego projektu i wyciąga z niego obiekty, kolumny i relacje.</summary>
public static class ProjectAnalyzer
{
    static readonly ModelTypeClass[] Tracked =
    {
        Table.TypeClass, View.TypeClass, Procedure.TypeClass, ScalarFunction.TypeClass,
        TableValuedFunction.TypeClass, DmlTrigger.TypeClass, Synonym.TypeClass, Sequence.TypeClass, TableType.TypeClass,
    };

    static readonly HashSet<string> TrackedNames = Tracked.Select(t => t.Name).ToHashSet();
    static readonly HashSet<string> CodeTypes = new() { "View", "Procedure", "ScalarFunction", "TableValuedFunction", "DmlTrigger" };
    static readonly HashSet<string> SystemSchemas = new(StringComparer.OrdinalIgnoreCase) { "sys", "INFORMATION_SCHEMA" };

    public static ProjectOutput Analyze(ProjectInput input)
    {
        var version = MapVersion(input.Dsp);
        var output = new ProjectOutput { Name = input.Name, Platform = version.ToString() };
        var model = new TSqlModel(version, new TSqlModelOptions());
        var scripts = new Dictionary<string, ScriptObject>(StringComparer.OrdinalIgnoreCase);
        var contents = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);

        foreach (var file in input.Files)
        {
            contents[file.Path] = file.Content;
            var errorsBefore = output.ParseErrors.Count;
            foreach (var so in ScriptAnalyzer.Analyze(file, output.ParseErrors)) scripts[so.Key] = so;
            if (output.ParseErrors.Count > errorsBefore) continue; // plik z błędem składni pomijamy w modelu
            try
            {
                model.AddOrUpdateObjects(file.Content, file.Path, new TSqlObjectOptions());
            }
            catch (DacModelException ex)
            {
                foreach (var m in ex.Messages.Take(5))
                    output.ParseErrors.Add(new ParseError { File = file.Path, Line = 0, Message = $"SQL{m.Number}: {m.Message}" });
            }
        }

        var unresolvedMessages = new List<string>();
        try
        {
            foreach (var m in model.Validate())
            {
                if (m.Number is 71501 or 71502) unresolvedMessages.Add(m.Message);
                if (m.Number is 71502 or 71561 or 71562 or 71501) continue; // nierozwiązane odwołania liczymy sami z relacji
                if (output.Messages.Count < 200) output.Messages.Add($"{m.MessageType} SQL{m.Number}: {m.Message}");
            }
        }
        catch (Exception ex) { output.Messages.Add("Validate failed: " + ex.Message); }

        var objects = model.GetObjects(DacQueryScopes.UserDefined, Tracked).ToList();
        var known = new Dictionary<string, TSqlObject>(StringComparer.OrdinalIgnoreCase);
        foreach (var o in objects)
        {
            var key = KeyOf(o.Name.Parts);
            if (key != null) known[key] = o;
        }

        // klucze główne i obce
        var pkCols = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        var fkCols = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        foreach (var pk in model.GetObjects(DacQueryScopes.UserDefined, PrimaryKeyConstraint.TypeClass))
            foreach (var c in pk.GetReferenced(PrimaryKeyConstraint.Columns)) pkCols.Add(string.Join(".", c.Name.Parts));

        var edges = new Dictionary<string, EdgeInfo>();
        var issues = new Dictionary<string, IssueInfo>();

        foreach (var fk in model.GetObjects(DacQueryScopes.UserDefined, ForeignKeyConstraint.TypeClass))
        {
            foreach (var c in fk.GetReferenced(ForeignKeyConstraint.Columns)) fkCols.Add(string.Join(".", c.Name.Parts));
            var host = fk.GetReferenced(ForeignKeyConstraint.Host).FirstOrDefault();
            var hostKey = host == null ? null : KeyOf(host.Name.Parts);
            if (hostKey == null) continue;
            foreach (var r in fk.GetReferencedRelationshipInstances(ForeignKeyConstraint.ForeignTable, DacQueryScopes.All))
                AddReference(hostKey, "Table", r, "fk", edges, issues, scripts);
        }

        foreach (var o in objects)
        {
            var key = KeyOf(o.Name.Parts);
            if (key == null) continue;
            var type = o.ObjectType.Name;
            var src = o.GetSourceInformation();
            scripts.TryGetValue(key, out var so);
            string def = so?.Text ?? "";
            if (def.Length == 0 && o.TryGetScript(out var gen)) def = gen;
            var info = new ObjectInfo
            {
                Key = key,
                Schema = Names.Clean(o.Name.Parts.Count > 1 ? o.Name.Parts[0] : "dbo"),
                Name = Names.Clean(o.Name.Parts.Last()),
                Type = type,
                File = src?.SourceName,
                Line = src?.StartLine ?? 0,
                Definition = def,
                Hash = Hash(def),
            };
            if (type is "Table" or "TableType") info.Columns = Columns(o, pkCols, fkCols);
            if (type is "Procedure" or "ScalarFunction" or "TableValuedFunction") info.Params = Params(o);
            output.Objects.Add(info);

            if (type == "DmlTrigger")
                foreach (var r in RelInstances(o, "TriggerObject"))
                    AddReference(key, type, r, "on", edges, issues, scripts);

            if (type == "Synonym")
                foreach (var r in RelInstances(o, "ForObject"))
                    AddReference(key, type, r, "ref", edges, issues, scripts);

            if (CodeTypes.Contains(type))
            {
                foreach (var r in o.GetReferencedRelationshipInstances(DacQueryScopes.All))
                {
                    var rel = r.Relationship.Name;
                    if (!rel.Contains("Dependencies", StringComparison.OrdinalIgnoreCase)) continue;
                    AddReference(key, type, r, null, edges, issues, scripts);
                }
            }

            if (so != null)
            {
                foreach (var d in so.Dynamic)
                    AddIssue(issues, new IssueInfo { Object = key, Kind = "dynamic", Ref = OneLine(d.Text), Line = d.Line, Snippet = Snippet(contents, so.File, d.Line) });
                foreach (var q in so.OpenQueries)
                    AddIssue(issues, new IssueInfo { Object = key, Kind = "openquery", Ref = q.Server, Server = q.Server, Line = q.Line, Snippet = Snippet(contents, so.File, q.Line), Message = OneLine(q.Query) });
            }
        }

        // nierozwiązane kolumny przez alias tabeli (o.[Kolumna]) DacFx zgłasza tylko komunikatem SQL71501
        var nameRx = new Regex(@"\[[^\]]+\](?:\.\[[^\]]+\])+(?:::\[[^\]]+\])?");
        foreach (var msg in unresolvedMessages)
        {
            var names = nameRx.Matches(msg).Select(x => x.Value).ToList();
            if (names.Count < 2) continue;
            var el = SplitName(names[0]);
            if (el.Count < 2) continue;
            var elKey = Names.Key(el[0], el[1]);
            if (!known.ContainsKey(elKey)) continue;
            var so = scripts.GetValueOrDefault(elKey);
            if (so != null && so.OpenQueries.Count > 0) continue;
            foreach (var cand in names.Skip(1))
            {
                if (cand.Contains("::")) continue;
                var c = SplitName(cand);
                if (c.Count < 3) continue;
                var tKey = Names.Key(c[0], c[1]);
                if (!known.ContainsKey(tKey) || tKey == elKey) continue;
                var line = so == null ? 0 : FindLine(so, c[2]);
                AddIssue(issues, new IssueInfo
                {
                    Object = elKey, Kind = "unresolved", Ref = tKey + "." + c[2].ToLowerInvariant(), Line = line,
                    Snippet = so == null ? null : Snippet(contents, so.File, line), Message = "Kolumna nie istnieje w tabeli (SQL71501)",
                });
                break;
            }
        }

        // trigger: zależność od własnej tabeli to relacja „on”, a nie odczyt
        foreach (var e in edges.Values.Where(e => e.Kind == "on").ToList())
            edges.Remove($"{e.From}|{e.To}|reads|");

        // jeśli procedura i czyta, i zapisuje tę samą tabelę, zostaje tylko zapis
        foreach (var e in edges.Values.Where(e => e.Kind == "writes").ToList())
            edges.Remove($"{e.From}|{e.To}|reads|{e.ExternalDb}");

        output.Edges = edges.Values.OrderBy(e => e.From).ThenBy(e => e.To).ToList();
        output.Issues = issues.Values.OrderBy(i => i.Object).ThenBy(i => i.Line).ToList();
        output.Objects = output.Objects.OrderBy(o => o.Key).ToList();
        return output;

        void AddReference(string fromKey, string fromType, ModelRelationshipInstance r, string? forcedKind,
            Dictionary<string, EdgeInfo> edgeMap, Dictionary<string, IssueInfo> issueMap, Dictionary<string, ScriptObject> scriptMap)
        {
            var parts = r.ObjectName.Parts.Select(Names.Clean).ToList();
            var ext = r.ObjectName.ExternalParts?.Select(Names.Clean).ToList() ?? new List<string>();
            var target = r.Object;
            string? toKey;
            string? targetType = null;
            if (target != null)
            {
                var t = target.ObjectType.Name;
                if (t is "Column")
                {
                    if (parts.Count < 3) return;
                    toKey = Names.Key(parts[0], parts[1]);
                    targetType = known.TryGetValue(toKey, out var owner) ? owner.ObjectType.Name : null;
                    if (targetType == null) return;
                }
                else if (TrackedNames.Contains(t))
                {
                    toKey = KeyOf(target.Name.Parts);
                    targetType = t;
                }
                else return; // typy danych, schematy, parametry itp.
            }
            else
            {
                if (parts.Count == 0) return;
                if (parts.Count >= 2 && SystemSchemas.Contains(parts[0])) return;
                if (parts.Count == 1 && (parts[0].StartsWith('#') || parts[0].StartsWith('@'))) return;
                // kolumna nierozwiązanej tabeli → raportujemy tabelę
                toKey = parts.Count >= 2 ? Names.Key(parts[0], parts[1]) : Names.Key(null, parts[0]);
                if (known.ContainsKey(toKey) && ext.Count == 0)
                {
                    targetType = known[toKey].ObjectType.Name;
                    // kolumn ze źródła OPENQUERY nie da się zweryfikować (ostrzeżenie SQL70558), więc ich nie zgłaszamy
                    if (parts.Count >= 3 && !(scriptMap.GetValueOrDefault(fromKey)?.OpenQueries.Count > 0))
                    {
                        // tabela istnieje, ale kolumny już nie (np. usunięta kolumna nadal używana w procedurze)
                        var so0 = scriptMap.GetValueOrDefault(fromKey);
                        var line0 = so0 == null ? 0 : FindLine(so0, parts[2]);
                        AddIssue(issueMap, new IssueInfo
                        {
                            Object = fromKey, Kind = "unresolved", Ref = toKey + "." + parts[2].ToLowerInvariant(), Line = line0,
                            Snippet = so0 == null ? null : Snippet(contents, so0.File, line0),
                            Message = "Kolumna nie istnieje w tabeli (SQL71502)",
                        });
                    }
                }
                else if (ext.Count > 0)
                {
                    var db = ext.Last();
                    var server = ext.Count > 1 ? ext[0] : null;
                    var kindExt = forcedKind ?? Classify(fromType, null, toKey, scriptMap.GetValueOrDefault(fromKey));
                    var eKey = $"{fromKey}|{toKey}|{kindExt}|{db}";
                    edgeMap.TryAdd(eKey, new EdgeInfo { From = fromKey, To = toKey, Kind = kindExt, ExternalDb = db, ExternalServer = server });
                    return;
                }
                else
                {
                    var so = scriptMap.GetValueOrDefault(fromKey);
                    // nazwa serwera z OPENQUERY(serwer, ...) nie jest obiektem bazy
                    if (so != null && parts.Count >= 1 && so.OpenQueries.Any(q => string.Equals(q.Server, parts.Last(), StringComparison.OrdinalIgnoreCase))) return;
                    var line = so == null ? 0 : FindLine(so, parts.Count >= 2 ? parts[1] : parts[0]);
                    AddIssue(issueMap, new IssueInfo
                    {
                        Object = fromKey, Kind = "unresolved", Ref = toKey, Line = line,
                        Snippet = so == null ? null : Snippet(contents, so.File, line),
                        Message = "Obiekt nie istnieje w projekcie (SQL71502)",
                    });
                    return;
                }
            }
            if (toKey == null || toKey == fromKey) return;
            var kind = forcedKind ?? Classify(fromType, targetType, toKey, scriptMap.GetValueOrDefault(fromKey));
            edgeMap.TryAdd($"{fromKey}|{toKey}|{kind}|", new EdgeInfo { From = fromKey, To = toKey, Kind = kind });
        }
    }

    static string Classify(string fromType, string? targetType, string toKey, ScriptObject? so)
    {
        if (targetType is "Procedure") return "calls";
        if (targetType is "ScalarFunction" or "TableValuedFunction") return "calls";
        if (fromType == "View") return "reads";
        if (so != null)
        {
            var bare = toKey.Contains('.') ? toKey[(toKey.IndexOf('.') + 1)..] : toKey;
            if (so.Writes.Contains(toKey) || so.Writes.Contains(bare)) return "writes";
        }
        return "reads";
    }

    static void AddIssue(Dictionary<string, IssueInfo> map, IssueInfo i)
    {
        map.TryAdd($"{i.Object}|{i.Kind}|{i.Ref}|{i.Line}", i);
    }

    static IEnumerable<ModelRelationshipInstance> RelInstances(TSqlObject o, string relName)
    {
        var rel = o.ObjectType.Relationships.FirstOrDefault(r => r.Name == relName);
        return rel == null ? Enumerable.Empty<ModelRelationshipInstance>() : o.GetReferencedRelationshipInstances(rel, DacQueryScopes.All);
    }

    static IEnumerable<TSqlObject> Rel(TSqlObject o, string relName)
    {
        var rel = o.ObjectType.Relationships.FirstOrDefault(r => r.Name == relName);
        return rel == null ? Enumerable.Empty<TSqlObject>() : o.GetReferenced(rel);
    }

    static T? Prop<T>(TSqlObject o, string name)
    {
        var p = o.ObjectType.Properties.FirstOrDefault(x => x.Name == name);
        if (p == null) return default;
        try { return o.GetProperty<T>(p); } catch { return default; }
    }

    static string TypeName(TSqlObject o)
    {
        var dt = Rel(o, "DataType").FirstOrDefault();
        if (dt != null && dt.Name.Parts.Count > 1 && !string.Equals(Names.Clean(dt.Name.Parts[0]), "sys", StringComparison.OrdinalIgnoreCase))
            return string.Join(".", dt.Name.Parts.Select(Names.Clean));
        var baseName = dt == null ? "" : Names.Clean(dt.Name.Parts.Last()).ToLowerInvariant();
        if (baseName.Length == 0) return "";
        if (Prop<bool>(o, "IsMax")) return $"{baseName}(max)";
        var len = Prop<int>(o, "Length");
        var prec = Prop<int>(o, "Precision");
        var scale = Prop<int>(o, "Scale");
        if (baseName is "char" or "varchar" or "nchar" or "nvarchar" or "binary" or "varbinary") return len > 0 ? $"{baseName}({len})" : baseName;
        if (baseName is "decimal" or "numeric") return prec > 0 ? $"{baseName}({prec},{scale})" : baseName;
        if (baseName is "datetime2" or "time" or "datetimeoffset") return $"{baseName}({scale})";
        return baseName;
    }

    static List<ColumnInfo> Columns(TSqlObject table, HashSet<string> pk, HashSet<string> fk)
    {
        var list = new List<ColumnInfo>();
        foreach (var c in Rel(table, "Columns"))
        {
            var full = string.Join(".", c.Name.Parts);
            var ct = Prop<ColumnType>(c, "ColumnType");
            list.Add(new ColumnInfo
            {
                Name = Names.Clean(c.Name.Parts.Last()),
                Type = TypeName(c),
                Nullable = Prop<bool>(c, "Nullable"),
                Identity = Prop<bool>(c, "IsIdentity"),
                Computed = ct == ColumnType.ComputedColumn,
                Pk = pk.Contains(full),
                Fk = fk.Contains(full),
            });
        }
        return list;
    }

    static List<ParamInfo> Params(TSqlObject o) =>
        Rel(o, "Parameters").Select(p => new ParamInfo
        {
            Name = Names.Clean(p.Name.Parts.Last()),
            Type = TypeName(p),
            Output = Prop<bool>(p, "IsOutput"),
        }).ToList();

    static List<string> SplitName(string bracketed) =>
        bracketed.Trim('[', ']').Split("].[").Select(Names.Clean).ToList();

    static string? KeyOf(IList<string> parts) => parts.Count switch
    {
        0 => null,
        1 => Names.Key(null, parts[0]),
        _ => Names.Key(parts[0], parts[1]),
    };

    static int FindLine(ScriptObject so, string name)
    {
        var lines = so.Text.Split('\n');
        var rx = new Regex(@"(?<![\w#@])\[?" + Regex.Escape(name) + @"\]?(?!\w)", RegexOptions.IgnoreCase);
        for (var i = 0; i < lines.Length; i++)
            if (i > 0 && rx.IsMatch(lines[i])) return so.StartLine + i;
        return so.StartLine;
    }

    static string? Snippet(Dictionary<string, string> contents, string file, int line)
    {
        if (line <= 0 || !contents.TryGetValue(file, out var text)) return null;
        var lines = text.Replace("\r", "").Split('\n');
        var from = Math.Max(0, line - 2);
        var to = Math.Min(lines.Length - 1, line);
        return string.Join("\n", lines[from..(to + 1)]);
    }

    static string OneLine(string s)
    {
        var t = Regex.Replace(s, @"\s+", " ").Trim();
        return t.Length > 200 ? t[..200] + "…" : t;
    }

    static string Hash(string text)
    {
        var norm = Regex.Replace(text, @"\s+", " ").Trim();
        return Convert.ToHexString(SHA1.HashData(Encoding.UTF8.GetBytes(norm)))[..16].ToLowerInvariant();
    }

    static SqlServerVersion MapVersion(string? dsp)
    {
        if (!string.IsNullOrEmpty(dsp))
        {
            var m = Regex.Match(dsp, @"(Sql\w*?)DatabaseSchemaProvider", RegexOptions.IgnoreCase);
            if (m.Success && Enum.TryParse<SqlServerVersion>(m.Groups[1].Value, true, out var v)) return v;
        }
        return SqlServerVersion.Sql160;
    }
}
