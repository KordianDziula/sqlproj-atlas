using System.Text;
using System.Text.Json;
using System.Text.Json.Serialization;

namespace AtlasEngine;

/// <summary>
/// atlas-engine: analizuje pliki projektów SSDT narzędziami Microsoftu (DacFx + ScriptDom).
/// Użycie:
///   atlas-engine analyze &lt;input.json&gt; [--out &lt;output.json&gt;]
///   atlas-engine version
/// Wejście: { "projects": [ { "name", "dsp", "files": [ { "path", "content" } ] } ] }
/// </summary>
public static class Program
{
    public const string Version = "1.0.0";

    public static readonly JsonSerializerOptions Json = new()
    {
        PropertyNamingPolicy = JsonNamingPolicy.CamelCase,
        DefaultIgnoreCondition = JsonIgnoreCondition.WhenWritingNull,
        WriteIndented = false,
    };

    public static int Main(string[] args)
    {
        Console.OutputEncoding = new UTF8Encoding(false);
        // komunikaty DacFx po angielsku niezależnie od języka systemu (parsujemy je)
        System.Globalization.CultureInfo.DefaultThreadCurrentUICulture = System.Globalization.CultureInfo.InvariantCulture;
        System.Globalization.CultureInfo.CurrentUICulture = System.Globalization.CultureInfo.InvariantCulture;
        if (args.Length == 0 || args[0] is "-h" or "--help")
        {
            Console.Error.WriteLine("Usage: atlas-engine analyze <input.json> [--out <output.json>] | atlas-engine version");
            return 2;
        }
        try
        {
            switch (args[0])
            {
                case "version":
                    Console.WriteLine(JsonSerializer.Serialize(new { engine = Version, dacfx = typeof(Microsoft.SqlServer.Dac.Model.TSqlModel).Assembly.GetName().Version?.ToString() }, Json));
                    return 0;
                case "analyze":
                    if (args.Length < 2) { Console.Error.WriteLine("Missing input file"); return 2; }
                    var input = JsonSerializer.Deserialize<EngineInput>(File.ReadAllText(args[1], Encoding.UTF8), Json)
                                ?? throw new InvalidOperationException("Empty input");
                    var result = new EngineOutput { Engine = Version };
                    foreach (var p in input.Projects)
                        result.Projects.Add(ProjectAnalyzer.Analyze(p));
                    var json = JsonSerializer.Serialize(result, Json);
                    var outIdx = Array.IndexOf(args, "--out");
                    if (outIdx > 0 && outIdx + 1 < args.Length) File.WriteAllText(args[outIdx + 1], json, new UTF8Encoding(false));
                    else Console.Out.Write(json);
                    return 0;
                default:
                    Console.Error.WriteLine($"Unknown command: {args[0]}");
                    return 2;
            }
        }
        catch (Exception ex)
        {
            Console.Error.WriteLine("atlas-engine error: " + ex);
            return 1;
        }
    }
}

public sealed class EngineInput
{
    public List<ProjectInput> Projects { get; set; } = new();
}

public sealed class ProjectInput
{
    public string Name { get; set; } = "";
    public string? Dsp { get; set; }
    public List<FileInput> Files { get; set; } = new();
}

public sealed class FileInput
{
    public string Path { get; set; } = "";
    public string Content { get; set; } = "";
}

public sealed class EngineOutput
{
    public string Engine { get; set; } = "";
    public List<ProjectOutput> Projects { get; set; } = new();
}

public sealed class ProjectOutput
{
    public string Name { get; set; } = "";
    public string Platform { get; set; } = "";
    public List<ObjectInfo> Objects { get; set; } = new();
    public List<EdgeInfo> Edges { get; set; } = new();
    public List<IssueInfo> Issues { get; set; } = new();
    public List<ParseError> ParseErrors { get; set; } = new();
    public List<string> Messages { get; set; } = new();
}

public sealed class ObjectInfo
{
    public string Key { get; set; } = "";
    public string Schema { get; set; } = "";
    public string Name { get; set; } = "";
    public string Type { get; set; } = "";
    public string? File { get; set; }
    public int Line { get; set; }
    public string Hash { get; set; } = "";
    public string? Definition { get; set; }
    public List<ColumnInfo>? Columns { get; set; }
    public List<ParamInfo>? Params { get; set; }
}

public sealed class ColumnInfo
{
    public string Name { get; set; } = "";
    public string Type { get; set; } = "";
    public bool Nullable { get; set; }
    public bool Identity { get; set; }
    public bool Pk { get; set; }
    public bool Fk { get; set; }
    public bool Computed { get; set; }
}

public sealed class ParamInfo
{
    public string Name { get; set; } = "";
    public string Type { get; set; } = "";
    public bool Output { get; set; }
}

public sealed class EdgeInfo
{
    public string From { get; set; } = "";
    public string To { get; set; } = "";
    public string Kind { get; set; } = "";
    /// <summary>Baza zewnętrzna, np. "$(Staging)" albo "ImportDb", gdy cel jest poza projektem.</summary>
    public string? ExternalDb { get; set; }
    public string? ExternalServer { get; set; }
}

public sealed class IssueInfo
{
    public string Object { get; set; } = "";
    /// <summary>unresolved | external | dynamic | openquery</summary>
    public string Kind { get; set; } = "";
    public string Ref { get; set; } = "";
    public string? Db { get; set; }
    public string? Server { get; set; }
    public int Line { get; set; }
    public string? Snippet { get; set; }
    public string? Message { get; set; }
}

public sealed class ParseError
{
    public string File { get; set; } = "";
    public int Line { get; set; }
    public string Message { get; set; } = "";
}
